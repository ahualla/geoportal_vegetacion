"""Pruebas del backend (sin necesidad de credenciales de Earth Engine)."""
import pytest

import app as app_module
import gee_service as gee
from config import INDICES, build_classes, classify_value

VALID = {"start": "2025-01-01", "end": "2025-03-31", "cloud": "30"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gee, "initialize", lambda force=False: True)
    monkeypatch.setattr(gee, "ensure_ready", lambda: None)
    application = app_module.create_app()
    application.config["TESTING"] = True
    return application.test_client()


# ----------------------------------------------------------------- config
def test_every_index_has_exactly_five_classes():
    for key in INDICES:
        classes = build_classes(key)
        assert len(classes) == 5
        assert [c["id"] for c in classes] == [1, 2, 3, 4, 5]
        assert len({c["color"] for c in classes}) == 5


def test_classify_value_boundaries():
    assert classify_value("NDVI", -0.2) == 1
    assert classify_value("NDVI", 0.10) == 1
    assert classify_value("NDVI", 0.1001) == 2
    assert classify_value("NDVI", 0.50) == 3
    assert classify_value("NDVI", 0.70) == 4
    assert classify_value("NDVI", 0.71) == 5
    assert classify_value("BIOMASA", 300) == 5


def test_public_config_endpoint(client):
    data = client.get("/api/config").get_json()
    assert [i["key"] for i in data["indices"]] == ["NDVI", "SAVI", "MSAVI", "EVI", "BIOMASA"]
    assert all(len(i["classes"]) == 5 for i in data["indices"])


# ------------------------------------------------------------------ vistas
def test_index_page_and_headers(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Geoportal" in resp.get_data(as_text=True)
    assert "Content-Security-Policy" in resp.headers
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_health(client):
    data = client.get("/api/health").get_json()
    assert data["status"] == "ok" and "gee_ready" in data


def test_cors_header_on_api_only(client):
    assert client.get("/api/health").headers["Access-Control-Allow-Origin"] == "*"
    assert "Access-Control-Allow-Origin" not in client.get("/").headers


def test_index_page_has_no_template_tags_and_inline_logo(client):
    html = client.get("/").get_data(as_text=True)
    assert "{{" not in html and "{%" not in html
    assert "<svg" in html and 'id="server-modal"' in html


def test_unknown_api_route_is_json_404(client):
    resp = client.get("/api/nada")
    assert resp.status_code == 404 and "error" in resp.get_json()


# -------------------------------------------------------------- validación
@pytest.mark.parametrize(
    "query",
    [
        {"index": "XYZ", **VALID},
        {"index": "NDVI", "start": "ayer", "end": "2025-03-31"},
        {"index": "NDVI", "start": "2025-03-31", "end": "2025-01-01"},
        {"index": "NDVI", "start": "2017-01-01", "end": "2017-02-01"},
        {"index": "NDVI", "start": "2023-01-01", "end": "2025-03-31"},  # > 366 días
        {"index": "NDVI", "start": "2025-01-01", "end": "2025-03-31", "cloud": "150"},
        {"index": "NDVI", "start": "2025-01-01", "end": "2025-03-31", "cloud": "x"},
        {"index": "NDVI", "start": "2025-01-01", "end": "2999-01-01"},
    ],
)
def test_tiles_rejects_invalid_queries(client, query):
    resp = client.get("/api/tiles", query_string=query)
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_tiles_ok_passes_clean_arguments(client, monkeypatch):
    seen = {}

    def fake(index, start, end, cloud):
        seen["args"] = (index, start, end, cloud)
        return {"tile_url": "https://example.test/{z}/{x}/{y}", "scenes": 10}

    monkeypatch.setattr(gee, "get_tiles", fake)
    resp = client.get("/api/tiles", query_string={"index": "ndvi", **VALID})
    assert resp.status_code == 200
    assert seen["args"] == ("NDVI", "2025-01-01", "2025-03-31", 30)


def test_biomass_ignores_dates(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(gee, "get_tiles", lambda *a: seen.setdefault("args", a) and {"tile_url": "x"})
    assert client.get("/api/tiles", query_string={"index": "BIOMASA"}).status_code == 200
    assert seen["args"] == ("BIOMASA", None, None, None)


def test_stats_ok(client, monkeypatch):
    monkeypatch.setattr(gee, "get_stats", lambda *a: {"classes": [], "valid_km2": 1})
    assert client.get("/api/stats", query_string={"index": "EVI", **VALID}).status_code == 200


def test_pixel_validation_and_ok(client, monkeypatch):
    monkeypatch.setattr(gee, "get_pixel", lambda *a: {"value": 0.5})
    base = {"index": "SAVI", **VALID}
    assert client.get("/api/pixel", query_string={**base, "lat": "-12.04", "lon": "-77.03"}).status_code == 200
    # Buenos Aires: fuera de Perú
    assert client.get("/api/pixel", query_string={**base, "lat": "-34.6", "lon": "-58.4"}).status_code == 400
    assert client.get("/api/pixel", query_string={**base, "lat": "a", "lon": "b"}).status_code == 400


# ------------------------------------------------------------------ errores
def test_gee_not_ready_returns_503_json(client, monkeypatch):
    def boom(*_a):
        raise gee.GeeNotReady("Google Earth Engine no está disponible.")

    monkeypatch.setattr(gee, "get_tiles", boom)
    resp = client.get("/api/tiles", query_string={"index": "NDVI", **VALID})
    assert resp.status_code == 503
    assert "no está disponible" in resp.get_json()["error"]


def test_no_data_returns_404_json(client, monkeypatch):
    def none(*_a):
        raise gee.NoDataError("Sin imágenes.")

    monkeypatch.setattr(gee, "get_tiles", none)
    resp = client.get("/api/tiles", query_string={"index": "NDVI", **VALID})
    assert resp.status_code == 404 and resp.get_json()["error"] == "Sin imágenes."
