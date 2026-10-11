
"""Capa de acceso a Google Earth Engine (GEE).

Responsabilidades:
  * Inicializar GEE mediante cuenta de servicio o credenciales locales.
  * Construir composiciones Sentinel-2 y calcular NDVI, SAVI, MSAVI y EVI.
  * Servir biomasa aérea como producto de referencia.
  * Clasificar índices en cinco rangos y recortarlos al territorio de Perú.
  * Entregar URL de teselas, estadísticas de área por clase y valores puntuales.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any, Callable

import ee

from config import INDICES, Settings, build_classes, classify_value

log = logging.getLogger("geoportal.gee")

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
BIOMASS_COLLECTION = "NASA/ORNL/biomass_carbon_density/v1"
BORDERS = "USDOS/LSIB_SIMPLE/2017"
SAVI_L = 0.5

TILE_TTL = min(Settings.CACHE_TTL_SECONDS, 4 * 3600)


# --------------------------------------------------------------------------- #
# Errores
# --------------------------------------------------------------------------- #
class GeeError(RuntimeError):
    status = 502


class GeeNotReady(GeeError):
    status = 503


class NoDataError(GeeError):
    status = 404


# --------------------------------------------------------------------------- #
# Caché con caducidad
# --------------------------------------------------------------------------- #
class TTLCache:
    def __init__(self, ttl: int, max_items: int = 256) -> None:
        self._ttl = ttl
        self._max = max_items
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None

            expires, value = item
            if expires < time.time():
                del self._data[key]
                return None

            return value

    def set(
        self,
        key: Any,
        value: Any,
        ttl: int | None = None,
    ) -> None:
        with self._lock:
            if len(self._data) >= self._max:
                oldest = min(
                    self._data,
                    key=lambda k: self._data[k][0],
                )
                del self._data[oldest]

            self._data[key] = (
                time.time() + (ttl or self._ttl),
                value,
            )


_cache = TTLCache(Settings.CACHE_TTL_SECONDS)


# --------------------------------------------------------------------------- #
# Inicialización
# --------------------------------------------------------------------------- #
_state: dict[str, Any] = {"ready": False, "error": None}
_init_lock = threading.Lock()


def initialize(force: bool = False) -> bool:
    """Inicializa Earth Engine y conserva el error para /api/health."""
    with _init_lock:
        if _state["ready"] and not force:
            return True

        try:
            project = Settings.GEE_PROJECT or None

            if (
                Settings.GEE_SERVICE_ACCOUNT
                and Settings.GEE_PRIVATE_KEY_FILE
            ):
                creds = ee.ServiceAccountCredentials(
                    Settings.GEE_SERVICE_ACCOUNT,
                    Settings.GEE_PRIVATE_KEY_FILE,
                )
                ee.Initialize(creds, project=project)
            else:
                ee.Initialize(project=project)

            _state.update(ready=True, error=None)
            log.info(
                "Google Earth Engine inicializado correctamente. "
                "Proyecto configurado: %s",
                project,
            )

        except Exception as exc:
            _state.update(ready=False, error=str(exc))
            log.exception(
                "No se pudo inicializar Google Earth Engine"
            )

        return _state["ready"]


def ensure_ready() -> None:
    if _state["ready"] or initialize():
        return

    raise GeeNotReady(
        "Google Earth Engine no está disponible. "
        "Revise las credenciales y la variable GEE_PROJECT."
    )


def status() -> dict[str, Any]:
    return {
        "ready": _state["ready"],
        "error": _state["error"],
    }


# --------------------------------------------------------------------------- #
# Mensajes de error
# --------------------------------------------------------------------------- #
def _friendly(message: str) -> str:
    low = message.lower()

    if "timed out" in low or "deadline" in low:
        return (
            "Google Earth Engine tardó demasiado en responder. "
            "Reduzca el período de análisis o inténtelo de nuevo."
        )

    if "quota" in low or "too many" in low or "429" in low:
        return (
            "Se alcanzó el límite de uso de Google Earth Engine. "
            "Espere un momento e inténtelo otra vez."
        )

    if "memory" in low:
        return (
            "El cálculo excedió la memoria disponible. "
            "Pruebe con un período más corto."
        )

    if (
        "permission" in low
        or "not signed up" in low
        or "not authorized" in low
        or "403" in low
    ):
        return (
            "Google Earth Engine rechazó una solicitud por permisos. "
            "Revise el proyecto GEE_PROJECT, el registro de Earth Engine "
            "y los permisos de la cuenta de servicio. Consulte los logs "
            "de Render para ver el error original."
        )

    return (
        "Earth Engine devolvió un error: "
        + message[:500]
    )


def _safe(fn: Callable[[], Any]) -> Any:
    """Ejecuta una llamada GEE y registra el error original."""
    try:
        return fn()

    except GeeError:
        raise

    except ee.EEException as exc:
        log.error(
            "Error original de Google Earth Engine: %s",
            str(exc),
            exc_info=True,
        )
        raise GeeError(_friendly(str(exc))) from exc

    except Exception as exc:
        log.exception(
            "Error inesperado al comunicarse con Google Earth Engine"
        )
        raise GeeError(
            "Error de comunicación con Google Earth Engine. "
            "Consulte los logs del servidor."
        ) from exc


# --------------------------------------------------------------------------- #
# Límites geográficos
# --------------------------------------------------------------------------- #
def _peru() -> ee.FeatureCollection:
    return (
        ee.FeatureCollection(BORDERS)
        .filter(ee.Filter.eq("country_na", "Peru"))
    )


# --------------------------------------------------------------------------- #
# Sentinel-2
# --------------------------------------------------------------------------- #
def _s2_collection(
    start: str,
    end: str,
    max_cloud: int,
    region: ee.Geometry,
) -> ee.ImageCollection:
    end_exclusive = (
        dt.date.fromisoformat(end) + dt.timedelta(days=1)
    ).isoformat()

    return (
        ee.ImageCollection(S2_COLLECTION)
        .filterBounds(region)
        .filterDate(start, end_exclusive)
        .filter(
            ee.Filter.lte(
                "CLOUDY_PIXEL_PERCENTAGE",
                max_cloud,
            )
        )
    )


def _mask_clouds(img: ee.Image) -> ee.Image:
    """Enmascara píxeles sin dato, saturados, sombras, nubes y cirros."""
    scl = img.select("SCL")

    bad = (
        scl.eq(0)
        .Or(scl.eq(1))
        .Or(scl.eq(3))
        .Or(scl.eq(8))
        .Or(scl.eq(9))
        .Or(scl.eq(10))
    )

    return img.updateMask(bad.Not())


# --------------------------------------------------------------------------- #
# Índices espectrales
# --------------------------------------------------------------------------- #
def _spectral_index(index: str, comp: ee.Image) -> ee.Image:
    nir = comp.select("B8")
    red = comp.select("B4")
    blue = comp.select("B2")

    if index == "NDVI":
        value = nir.subtract(red).divide(nir.add(red))

    elif index == "SAVI":
        value = (
            nir.subtract(red)
            .multiply(1 + SAVI_L)
            .divide(nir.add(red).add(SAVI_L))
        )

    elif index == "MSAVI":
        value = comp.expression(
            "(2 * NIR + 1 - sqrt((2 * NIR + 1) * "
            "(2 * NIR + 1) - 8 * (NIR - RED))) / 2",
            {"NIR": nir, "RED": red},
        )

    elif index == "EVI":
        value = comp.expression(
            "2.5 * (NIR - RED) / "
            "(NIR + 6 * RED - 7.5 * BLUE + 1)",
            {
                "NIR": nir,
                "RED": red,
                "BLUE": blue,
            },
        )

    else:
        raise ValueError(f"Índice desconocido: {index}")

    return value.rename("v")


def _build(
    index: str,
    start: str | None,
    end: str | None,
    max_cloud: int | None,
    region: ee.Geometry,
):
    """Devuelve la imagen de valores 'v' y la colección Sentinel-2."""
    if index == "BIOMASA":
        image = (
            ee.ImageCollection(BIOMASS_COLLECTION)
            .select("agb")
            .mean()
            .rename("v")
        )
        return image, None

    if not start or not end or max_cloud is None:
        raise ValueError(
            "Se requieren fecha inicial, fecha final y nubosidad máxima."
        )

    col = _s2_collection(
        start,
        end,
        max_cloud,
        region,
    )

    comp = (
        col.map(_mask_clouds)
        .select(["B2", "B4", "B8"])
        .median()
        .multiply(0.0001)
    )

    return _spectral_index(index, comp), col


def _classify(value: ee.Image, index: str) -> ee.Image:
    """Clasifica en cinco rangos (1-5), conservando la máscara original."""
    cls = ee.Image(1)

    for threshold in INDICES[index]["thresholds"]:
        cls = cls.add(value.gt(threshold))

    return (
        cls.updateMask(value.mask())
        .toInt8()
        .rename("cls")
    )


# --------------------------------------------------------------------------- #
# API pública: límite de Perú
# --------------------------------------------------------------------------- #
def get_boundary() -> dict:
    """Devuelve el límite de Perú como una entidad GeoJSON."""
    ensure_ready()

    key = ("boundary",)
    cached = _cache.get(key)

    if cached is not None:
        return cached

    # Registrar el nombre de la operación ayuda a identificar errores 403.
    log.info(
        "Consultando límite de Perú en Earth Engine: colección=%s",
        BORDERS,
    )

    geom = _safe(
        lambda: _peru().geometry().simplify(1000).getInfo()
    )

    if not isinstance(geom, dict) or not geom.get("type"):
        log.error(
            "Earth Engine devolvió una geometría inválida: %r",
            geom,
        )
        raise GeeError(
            "Earth Engine no devolvió una geometría válida para Perú."
        )

    if geom["type"] == "GeometryCollection":
        geometries = geom.get("geometries", [])

        if not geometries:
            raise GeeError(
                "Earth Engine devolvió una geometría vacía para Perú."
            )

        geometry = {
            "type": "GeometryCollection",
            "geometries": geometries,
        }

    else:
        coordinates = geom.get("coordinates")

        if coordinates is None:
            log.error(
                "Geometría sin coordenadas. Tipo recibido: %s",
                geom.get("type"),
            )
            raise GeeError(
                "La geometría de Perú no contiene coordenadas."
            )

        geometry = {
            "type": geom["type"],
            "coordinates": coordinates,
        }

    feature = {
        "type": "Feature",
        "properties": {"name": "Perú"},
        "geometry": geometry,
    }

    _cache.set(key, feature, ttl=24 * 3600)

    log.info("Límite de Perú cargado correctamente.")
    return feature


# --------------------------------------------------------------------------- #
# API pública: teselas de mapas
# --------------------------------------------------------------------------- #
def get_tiles(
    index: str,
    start: str | None,
    end: str | None,
    max_cloud: int | None,
) -> dict:
    """Devuelve la URL de teselas del índice clasificado."""
    ensure_ready()

    key = ("tiles", index, start, end, max_cloud)
    cached = _cache.get(key)

    if cached is not None:
        return cached

    if index not in INDICES:
        raise ValueError(f"Índice no válido: {index}")

    log.info(
        "Generando teselas: índice=%s, fechas=%s a %s",
        index,
        start,
        end,
    )

    meta = INDICES[index]
    peru = _safe(lambda: _peru().geometry())

    value, col = _build(
        index,
        start,
        end,
        max_cloud,
        peru,
    )

    scenes = None

    if col is not None:
        scenes = _safe(lambda: col.size().getInfo())

        if not scenes:
            raise NoDataError(
                "No hay imágenes Sentinel-2 para ese período y nubosidad. "
                "Amplíe las fechas o aumente la nubosidad máxima."
            )

    classified = _classify(value, index).clip(peru)

    vis = {
        "min": 1,
        "max": 5,
        "palette": meta["palette"],
    }

    map_id = _safe(lambda: classified.getMapId(vis))

    result = {
        "index": index,
        "tile_url": map_id["tile_fetcher"].url_format,
        "scenes": scenes,
        "period": (
            {"start": start, "end": end}
            if start
            else None
        ),
        "classes": build_classes(index),
    }

    _cache.set(key, result, ttl=TILE_TTL)

    log.info("Teselas generadas correctamente para %s.", index)
    return result


# --------------------------------------------------------------------------- #
# API pública: estadísticas de área
# --------------------------------------------------------------------------- #
def get_stats(
    index: str,
    start: str | None,
    end: str | None,
    max_cloud: int | None,
) -> dict:
    """Devuelve área en km² y porcentaje por clase dentro de Perú."""
    ensure_ready()

    if index not in INDICES:
        raise ValueError(f"Índice no válido: {index}")

    key = ("stats", index, start, end, max_cloud)
    cached = _cache.get(key)

    if cached is not None:
        return cached

    peru = _safe(lambda: _peru().geometry())

    value, _ = _build(
        index,
        start,
        end,
        max_cloud,
        peru,
    )

    classified = _classify(value, index).clip(peru)

    scale = Settings.STATS_SCALE_M
    area_km2 = ee.Image.pixelArea().divide(1e6)

    grouped = (
        area_km2.updateMask(classified.mask())
        .addBands(classified)
        .reduceRegion(
            reducer=ee.Reducer.sum().group(
                groupField=1,
                groupName="cls",
            ),
            geometry=peru,
            scale=scale,
            maxPixels=1e13,
            bestEffort=True,
            tileScale=8,
        )
    )

    total = area_km2.reduceRegion(
        reducer=ee.Reducer.sum(),
        geometry=peru,
        scale=scale,
        maxPixels=1e13,
        bestEffort=True,
        tileScale=4,
    )

    out = _safe(
        lambda: ee.Dictionary({
            "groups": grouped.get("groups"),
            "total": total.get("area"),
        }).getInfo()
    )

    by_class = {
        int(group["cls"]): float(group["sum"])
        for group in (out.get("groups") or [])
    }

    total_km2 = float(out.get("total") or 0.0)
    valid_km2 = sum(by_class.values())

    classes = []

    for c in build_classes(index):
        area = by_class.get(c["id"], 0.0)

        classes.append({
            "id": c["id"],
            "label": c["label"],
            "range": c["range"],
            "color": c["color"],
            "area_km2": round(area, 1),
            "percent": (
                round(100.0 * area / valid_km2, 2)
                if valid_km2
                else 0.0
            ),
        })

    result = {
        "index": index,
        "classes": classes,
        "total_km2": round(total_km2, 1),
        "valid_km2": round(valid_km2, 1),
        "nodata_km2": round(
            max(total_km2 - valid_km2, 0.0),
            1,
        ),
        "coverage_percent": (
            round(100.0 * valid_km2 / total_km2, 1)
            if total_km2
            else 0.0
        ),
        "scale_m": scale,
    }

    _cache.set(key, result)

    log.info("Estadísticas calculadas correctamente para %s.", index)
    return result


# --------------------------------------------------------------------------- #
# API pública: consulta de un píxel
# --------------------------------------------------------------------------- #
def get_pixel(
    index: str,
    lat: float,
    lon: float,
    start: str | None,
    end: str | None,
    max_cloud: int | None,
) -> dict:
    """Devuelve el valor del índice y su clase en un punto del mapa."""
    ensure_ready()

    if index not in INDICES:
        raise ValueError(f"Índice no válido: {index}")

    meta = INDICES[index]
    point = ee.Geometry.Point([lon, lat])
    peru = _safe(lambda: _peru().geometry())

    value, _ = _build(
        index,
        start,
        end,
        max_cloud,
        point,
    )

    sample = value.clip(peru).reduceRegion(
        reducer=ee.Reducer.first(),
        geometry=point,
        scale=meta["native_scale"],
        maxPixels=1e6,
    )

    raw = _safe(lambda: sample.get("v").getInfo())

    if raw is None:
        return {
            "index": index,
            "value": None,
            "lat": lat,
            "lon": lon,
        }

    cls_id = classify_value(index, raw)
    cls = build_classes(index)[cls_id - 1]

    return {
        "index": index,
        "value": round(float(raw), 4),
        "class": cls_id,
        "label": cls["label"],
        "range": cls["range"],
        "color": cls["color"],
        "unit": meta["unit"],
        "lat": lat,
        "lon": lon,
    }
