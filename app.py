"""Servidor Flask del Geoportal de Índices de Vegetación - Perú.

Endpoints:
    GET /                 Visor web
    GET /api/health       Estado del servidor y de Google Earth Engine
    GET /api/config       Índices, rangos, colores y valores por defecto
    GET /api/boundary     Límite de Perú (GeoJSON)
    GET /api/tiles        URL de teselas del índice clasificado en 5 rangos
    GET /api/stats        Área y porcentaje por clase dentro de Perú
    GET /api/pixel        Valor del índice en un punto
"""
from __future__ import annotations

import datetime as dt
import logging

from flask import Flask, jsonify, render_template, request

import gee_service as gee
from config import INDICES, PERU_VIEW, Settings, public_config

log = logging.getLogger("geoportal")

# Sentinel-2 SR en GEE tiene cobertura confiable sobre Perú desde 2018.
MIN_DATE = dt.date(2018, 1, 1)
PERU_LAT = (PERU_VIEW["max_bounds"][0][0], PERU_VIEW["max_bounds"][1][0])
PERU_LON = (PERU_VIEW["max_bounds"][0][1], PERU_VIEW["max_bounds"][1][1])

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://unpkg.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self'; "
    "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
)


class ApiError(Exception):
    """Error de validación de la petición (se devuelve como JSON)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------- #
# Validación de parámetros
# --------------------------------------------------------------------------- #
def _parse_query() -> tuple[str, str | None, str | None, int | None]:
    """Valida index/start/end/cloud. Biomasa ignora fechas y nubosidad."""
    index = (request.args.get("index") or "").strip().upper()
    if index not in INDICES:
        raise ApiError("Índice no válido. Use: " + ", ".join(INDICES) + ".")
    if not INDICES[index]["temporal"]:
        return index, None, None, None

    try:
        start = dt.date.fromisoformat(request.args.get("start", ""))
        end = dt.date.fromisoformat(request.args.get("end", ""))
    except ValueError:
        raise ApiError("Fechas no válidas. Use el formato AAAA-MM-DD.") from None

    if start < MIN_DATE:
        raise ApiError(f"La fecha inicial no puede ser anterior a {MIN_DATE.isoformat()}.")
    if end < start:
        raise ApiError("La fecha final debe ser igual o posterior a la inicial.")
    if end > dt.date.today() + dt.timedelta(days=1):
        raise ApiError("La fecha final no puede estar en el futuro.")
    if (end - start).days > Settings.MAX_RANGE_DAYS:
        raise ApiError(f"El período máximo es de {Settings.MAX_RANGE_DAYS} días.")

    try:
        cloud = int(request.args.get("cloud", Settings.DEFAULT_CLOUD))
    except ValueError:
        raise ApiError("La nubosidad debe ser un número entero.") from None
    if not 0 <= cloud <= 100:
        raise ApiError("La nubosidad debe estar entre 0 y 100.")

    return index, start.isoformat(), end.isoformat(), cloud


def _parse_point() -> tuple[float, float]:
    try:
        lat = float(request.args.get("lat", ""))
        lon = float(request.args.get("lon", ""))
    except ValueError:
        raise ApiError("Coordenadas no válidas.") from None
    if not (PERU_LAT[0] <= lat <= PERU_LAT[1] and PERU_LON[0] <= lon <= PERU_LON[1]):
        raise ApiError("El punto está fuera del territorio peruano.")
    return lat, lon


# --------------------------------------------------------------------------- #
# Aplicación
# --------------------------------------------------------------------------- #
def create_app() -> Flask:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.json.ensure_ascii = False  # tildes legibles en las respuestas JSON

    gee.initialize()  # no lanza: el estado se consulta en /api/health

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
            if Settings.CORS_ORIGINS:
                resp.headers["Access-Control-Allow-Origin"] = Settings.CORS_ORIGINS
                if Settings.CORS_ORIGINS != "*":
                    resp.headers["Vary"] = "Origin"
        return resp

    # ----- Vistas ----- #
    @app.get("/")
    def index():
        return render_template("index.html")

    # ----- API ----- #
    @app.get("/api/health")
    def health():
        st = gee.status()
        return jsonify({"status": "ok", "gee_ready": st["ready"], "gee_error": st["error"]})

    @app.get("/api/config")
    def config():
        return jsonify(public_config())

    @app.get("/api/boundary")
    def boundary():
        return jsonify(gee.get_boundary())

    @app.get("/api/tiles")
    def tiles():
        return jsonify(gee.get_tiles(*_parse_query()))

    @app.get("/api/stats")
    def stats():
        return jsonify(gee.get_stats(*_parse_query()))

    @app.get("/api/pixel")
    def pixel():
        index_key, start, end, cloud = _parse_query()
        lat, lon = _parse_point()
        return jsonify(gee.get_pixel(index_key, lat, lon, start, end, cloud))

    # ----- Errores ----- #
    @app.errorhandler(ApiError)
    def _api_error(err: ApiError):
        return jsonify({"error": err.message}), err.status

    @app.errorhandler(gee.GeeError)
    def _gee_error(err: gee.GeeError):
        return jsonify({"error": str(err)}), err.status

    @app.errorhandler(404)
    def _not_found(_err):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Recurso no encontrado."}), 404
        return "Página no encontrada", 404

    @app.errorhandler(500)
    def _server_error(err):
        log.exception("Error interno: %s", err)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Error interno del servidor."}), 500
        return "Error interno del servidor", 500

    return app


if __name__ == "__main__":
    create_app().run(host=Settings.HOST, port=Settings.PORT, debug=Settings.DEBUG)
