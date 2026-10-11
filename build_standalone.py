"""Genera `geoportal.html`: una sola página con CSS, JavaScript y configuración
embebidos, que se puede abrir directamente (doble clic o desde el celular).

El HTML independiente NO calcula nada por sí solo: para mostrar capas necesita
que el servidor Python (app.py) esté accesible. Al abrirlo pedirá su dirección
(o puede fijarla al generar el archivo con --api-base).

Uso:
    python build_standalone.py
    python build_standalone.py --api-base https://mi-geoportal.onrender.com --out geoportal.html
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config import public_config  # noqa: E402

CSS_TAG = '<link rel="stylesheet" href="/static/css/style.css">'
JS_TAG = '<script src="/static/js/app.js"></script>'


def build(out: pathlib.Path, api_base: str = "") -> pathlib.Path:
    html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    for tag in (CSS_TAG, JS_TAG):
        if tag not in html:
            raise SystemExit(f"No se encontró la etiqueta esperada en index.html: {tag}")
    if "</script" in js or "</style" in css:
        raise SystemExit("El CSS/JS contiene una etiqueta de cierre que rompería el HTML incrustado.")

    config = json.dumps(public_config(), ensure_ascii=False).replace("</", "<\\/")
    boot = f"window.GEOPORTAL_CONFIG = {config};\nwindow.GEOPORTAL_API_BASE = {json.dumps(api_base.rstrip('/'))};"

    html = html.replace(CSS_TAG, f"<style>\n{css}\n</style>")
    html = html.replace(JS_TAG, f"<script>\n{boot}\n</script>\n<script>\n{js}\n</script>")

    if "/static/" in html:
        raise SystemExit("Quedaron referencias a /static/ sin incrustar.")

    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="geoportal.html", help="archivo de salida (por defecto geoportal.html)")
    parser.add_argument("--api-base", default="", help="URL del servidor, p. ej. https://mi-geoportal.onrender.com")
    args = parser.parse_args()
    path = build(pathlib.Path(args.out), args.api_base)
    print(f"Generado: {path} ({path.stat().st_size / 1024:.0f} KB)")
