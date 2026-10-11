"""Configuración central del Geoportal de Índices de Vegetación - Perú.

Este módulo es la única fuente de verdad para los índices, sus 5 rangos
(clases), colores y parámetros generales. El frontend consume estos datos a
través de /api/config, de modo que la leyenda siempre coincide con la
clasificación que calcula el servidor en Google Earth Engine.
"""
from __future__ import annotations

import os

try:  # python-dotenv es opcional: si no está instalado se usan variables del sistema
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


class Settings:
    """Parámetros de ejecución (se leen de variables de entorno / archivo .env)."""

    # Google Earth Engine
    GEE_PROJECT = os.getenv("GEE_PROJECT", "").strip()
    GEE_SERVICE_ACCOUNT = os.getenv("GEE_SERVICE_ACCOUNT", "").strip()
    GEE_PRIVATE_KEY_FILE = os.getenv("GEE_PRIVATE_KEY_FILE", "").strip()

    # Servidor
    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = _env_int("PORT", 8080)
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

    # Origen permitido para llamar a /api desde otra página (CORS). "*" permite
    # abrir geoportal.html desde cualquier lugar; en producción puede fijarse
    # a un dominio concreto. Vacío desactiva CORS.
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").strip()

    # Caché en memoria (segundos) y resolución de las estadísticas (metros)
    CACHE_TTL_SECONDS = _env_int("CACHE_TTL_SECONDS", 6 * 3600)
    STATS_SCALE_M = _env_int("STATS_SCALE_M", 1000)

    # Límites de consulta
    MAX_RANGE_DAYS = 366
    DEFAULT_RANGE_DAYS = 90
    DEFAULT_CLOUD = 30


# Vista del mapa: solo se permite navegar sobre el territorio peruano.
PERU_VIEW = {
    "center": [-9.19, -75.02],
    "zoom": 5,
    # Extensión del territorio peruano [[sur, oeste], [norte, este]]
    "bounds": [[-18.40, -81.40], [-0.03, -68.60]],
    # Límite de navegación (margen holgado alrededor de Perú)
    "max_bounds": [[-20.5, -84.0], [2.5, -66.0]],
}

CLASS_LABELS = ["Muy baja", "Baja", "Moderada", "Alta", "Muy alta"]

VEG_DESCRIPTIONS = [
    "Agua, nieve, roca o suelo desnudo",
    "Vegetación escasa o muy dispersa",
    "Vegetación moderada (pastizal, matorral, cultivos)",
    "Vegetación densa",
    "Vegetación muy densa (bosque húmedo)",
]

BIOMASS_DESCRIPTIONS = [
    "Sin cobertura leñosa o muy escasa",
    "Biomasa baja (pastizal, matorral)",
    "Biomasa media (bosque seco o secundario)",
    "Biomasa alta (bosque denso)",
    "Biomasa muy alta (bosque húmedo maduro)",
]

# Paletas secuenciales (de menor a mayor valor)
VEG_PALETTE = ["#8c510a", "#dfc27d", "#c7e9b4", "#41ab5d", "#00441b"]
BIOMASS_PALETTE = ["#ffffb2", "#c2e699", "#78c679", "#238443", "#004529"]

# Cada índice define 4 umbrales -> 5 clases:
#   clase 1: v <= t1 | clase 2: t1 < v <= t2 | clase 3: t2 < v <= t3
#   clase 4: t3 < v <= t4 | clase 5: v > t4
INDICES: dict[str, dict] = {
    "NDVI": {
        "name": "NDVI",
        "short": "Vigor vegetal",
        "full_name": "Índice de Vegetación de Diferencia Normalizada",
        "formula": "(NIR − RED) / (NIR + RED)",
        "thresholds": [0.10, 0.30, 0.50, 0.70],
        "decimals": 2,
        "unit": "",
        "temporal": True,
        "native_scale": 10,
        "palette": VEG_PALETTE,
        "descriptions": VEG_DESCRIPTIONS,
        "source": "Copernicus Sentinel-2 SR Harmonizado, 10 m (composición de mediana)",
        "note": "Indicador general del vigor y la densidad de la vegetación verde.",
    },
    "SAVI": {
        "name": "SAVI",
        "short": "Ajuste de suelo",
        "full_name": "Índice de Vegetación Ajustado al Suelo",
        "formula": "(1 + L) · (NIR − RED) / (NIR + RED + L),  L = 0,5",
        "thresholds": [0.10, 0.25, 0.40, 0.55],
        "decimals": 2,
        "unit": "",
        "temporal": True,
        "native_scale": 10,
        "palette": VEG_PALETTE,
        "descriptions": VEG_DESCRIPTIONS,
        "source": "Copernicus Sentinel-2 SR Harmonizado, 10 m (composición de mediana)",
        "note": "Reduce la influencia del brillo del suelo; útil en zonas de vegetación dispersa.",
    },
    "MSAVI": {
        "name": "MSAVI",
        "short": "Suelo modificado",
        "full_name": "Índice de Vegetación Ajustado al Suelo Modificado",
        "formula": "(2·NIR + 1 − √((2·NIR + 1)² − 8·(NIR − RED))) / 2",
        "thresholds": [0.10, 0.25, 0.40, 0.55],
        "decimals": 2,
        "unit": "",
        "temporal": True,
        "native_scale": 10,
        "palette": VEG_PALETTE,
        "descriptions": VEG_DESCRIPTIONS,
        "source": "Copernicus Sentinel-2 SR Harmonizado, 10 m (composición de mediana)",
        "note": "Variante de SAVI que ajusta automáticamente el factor de suelo.",
    },
    "EVI": {
        "name": "EVI",
        "short": "Vegetación mejorada",
        "full_name": "Índice de Vegetación Mejorado",
        "formula": "2,5 · (NIR − RED) / (NIR + 6·RED − 7,5·BLUE + 1)",
        "thresholds": [0.10, 0.25, 0.40, 0.60],
        "decimals": 2,
        "unit": "",
        "temporal": True,
        "native_scale": 10,
        "palette": VEG_PALETTE,
        "descriptions": VEG_DESCRIPTIONS,
        "source": "Copernicus Sentinel-2 SR Harmonizado, 10 m (composición de mediana)",
        "note": "Menos saturado que NDVI en bosques densos y corrige efectos atmosféricos.",
    },
    "BIOMASA": {
        "name": "Biomasa",
        "short": "Biomasa aérea",
        "full_name": "Biomasa aérea (AGB)",
        "formula": "Densidad de biomasa aérea en Mg/ha (producto de referencia)",
        "thresholds": [25, 75, 150, 250],
        "decimals": 0,
        "unit": "Mg/ha",
        "temporal": False,
        "native_scale": 300,
        "palette": BIOMASS_PALETTE,
        "descriptions": BIOMASS_DESCRIPTIONS,
        "source": "NASA ORNL DAAC - Biomass Carbon Density (≈2010, 300 m)",
        "note": "Producto estático de referencia: no depende del período de fechas.",
    },
}


def _fmt(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}".replace(".", ",") if decimals else f"{int(value)}"


def build_classes(key: str) -> list[dict]:
    """Devuelve las 5 clases del índice con rango, etiqueta, color y límites."""
    meta = INDICES[key]
    t, d, unit = meta["thresholds"], meta["decimals"], meta["unit"]
    sfx = f" {unit}" if unit else ""
    ranges = (
        [f"≤ {_fmt(t[0], d)}{sfx}"]
        + [f"{_fmt(t[i - 1], d)} – {_fmt(t[i], d)}{sfx}" for i in range(1, len(t))]
        + [f"> {_fmt(t[-1], d)}{sfx}"]
    )
    classes = []
    for i in range(5):
        classes.append(
            {
                "id": i + 1,
                "label": CLASS_LABELS[i],
                "description": meta["descriptions"][i],
                "range": ranges[i],
                "color": meta["palette"][i],
                "min": None if i == 0 else t[i - 1],
                "max": None if i == 4 else t[i],
            }
        )
    return classes


def classify_value(key: str, value: float) -> int:
    """Clase (1-5) de un valor; replica exactamente la lógica usada en Earth Engine."""
    return 1 + sum(1 for t in INDICES[key]["thresholds"] if value > t)


def public_config() -> dict:
    """Configuración que consume el frontend."""
    return {
        "view": PERU_VIEW,
        "defaults": {
            "cloud": Settings.DEFAULT_CLOUD,
            "range_days": Settings.DEFAULT_RANGE_DAYS,
            "max_range_days": Settings.MAX_RANGE_DAYS,
            "stats_scale_m": Settings.STATS_SCALE_M,
        },
        "indices": [
            {
                "key": key,
                "name": meta["name"],
                "short": meta["short"],
                "full_name": meta["full_name"],
                "formula": meta["formula"],
                "source": meta["source"],
                "note": meta["note"],
                "unit": meta["unit"],
                "decimals": meta["decimals"],
                "temporal": meta["temporal"],
                "classes": build_classes(key),
            }
            for key, meta in INDICES.items()
        ],
    }
