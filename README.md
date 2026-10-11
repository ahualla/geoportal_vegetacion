# Geoportal de Índices de Vegetación · Perú

Visor web restringido al territorio peruano que muestra **NDVI, SAVI, MSAVI, EVI y biomasa aérea**, cada uno clasificado en **5 rangos**, con leyenda, estadísticas de área por rango (km² y %) y consulta del valor al hacer clic en el mapa.

- **Backend:** Python (Flask) + Google Earth Engine (`earthengine-api`)
- **Frontend:** HTML + CSS + JavaScript (Leaflet), sin frameworks ni paso de compilación
- **Procesamiento:** en Earth Engine; el servidor solo entrega URL de teselas y resultados JSON

## 1. Requisitos

- Python 3.10 o superior
- Una cuenta de Google con acceso a [Google Earth Engine](https://earthengine.google.com/) y un **proyecto de Google Cloud** registrado en Earth Engine (gratuito para uso no comercial)
- Conexión a Internet (Earth Engine, mapas base y Leaflet desde CDN)

## 2. Instalación

```bash
cd geoportal_peru
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
```

Edite `.env` y complete al menos `GEE_PROJECT`.

## 3. Autenticación con Earth Engine

**Opción A: desarrollo local (la más simple).** Deje vacías `GEE_SERVICE_ACCOUNT` y `GEE_PRIVATE_KEY_FILE` y ejecute una vez:

```bash
earthengine authenticate
```

**Opción B: servidor / producción (cuenta de servicio).**

1. En Google Cloud cree una cuenta de servicio y descargue su clave JSON.
2. Registre esa cuenta de servicio en Earth Engine (consola de Earth Engine → proyecto).
3. Guarde la clave en `keys/service-account.json` (la carpeta `keys/` está en `.gitignore`).
4. En `.env`:

```
GEE_PROJECT=su-proyecto-gcp
GEE_SERVICE_ACCOUNT=geoportal@su-proyecto-gcp.iam.gserviceaccount.com
GEE_PRIVATE_KEY_FILE=keys/service-account.json
```

## 4. Ejecución

```bash
python app.py
```

Abra <http://127.0.0.1:8080>. La insignia superior derecha indica si Earth Engine está conectado.

Producción con Gunicorn (los cálculos pueden tardar, por eso el `timeout` amplio):

```bash
gunicorn --bind 0.0.0.0:8080 --workers 2 --threads 4 --timeout 300 "app:create_app()"
```

Con Docker:

```bash
docker build -t geoportal-peru .
docker run --rm -p 8080:8080 --env-file .env \
  -v "$PWD/keys:/app/keys:ro" geoportal-peru
```

## 4b. Versión de un solo archivo (`geoportal.html`)

Si abre `templates/index.html` suelto en el navegador o el celular se ve sin estilos (el CSS y el JS son archivos aparte). Para eso existe una versión autónoma:

```bash
python build_standalone.py                                   # genera geoportal.html
python build_standalone.py --api-base https://mi-servidor.com --out geoportal.html
```

`geoportal.html` incluye CSS, JavaScript y configuración, y se puede abrir con doble clic o desde el celular. **No calcula nada por sí solo**: necesita el servidor Python en ejecución y, al abrirlo, pide su dirección (o puede fijarla con `--api-base`). Desde un celular, `localhost` no sirve: use la IP de su PC en la red Wi-Fi (por ejemplo `http://192.168.1.20:8080`, ejecutando `HOST=0.0.0.0 python app.py`) o publique el servidor en un servicio con HTTPS.

## 5. Estructura

```
geoportal_peru/
├── app.py               # Servidor Flask, validación y endpoints REST
├── gee_service.py       # Earth Engine: composiciones, índices, clases, estadísticas
├── config.py            # Índices, 5 rangos, colores y parámetros (fuente única)
├── templates/index.html # Interfaz
├── static/
│   ├── css/style.css    # Estilos (responsive)
│   ├── js/app.js        # Lógica del visor (Leaflet)
│   └── img/favicon.svg
├── tests/test_api.py    # Pruebas del backend
├── requirements.txt · requirements-dev.txt · .env.example · Dockerfile
```

## 6. Endpoints

| Ruta | Descripción |
|---|---|
| `GET /api/health` | Estado del servidor y de Earth Engine |
| `GET /api/config` | Índices, rangos y colores |
| `GET /api/boundary` | Límite de Perú (GeoJSON) |
| `GET /api/tiles?index=NDVI&start=2026-01-01&end=2026-03-31&cloud=30` | URL de teselas del índice clasificado |
| `GET /api/stats?...` (mismos parámetros) | Área y porcentaje por rango |
| `GET /api/pixel?...&lat=-12.04&lon=-77.03` | Valor y rango en un punto |

`index` admite `NDVI`, `SAVI`, `MSAVI`, `EVI`, `BIOMASA`. Para `BIOMASA` se ignoran fechas y nubosidad.

## 7. Metodología

**Índices espectrales.** Composición de **mediana** de Sentinel-2 SR Harmonizado (`COPERNICUS/S2_SR_HARMONIZED`, 10 m) para el período elegido. Se descartan píxeles sin dato, saturados, sombras, nubes y cirros con la banda `SCL`, y escenas con nubosidad superior al umbral. Reflectancia = DN × 0,0001.

| Índice | Fórmula |
|---|---|
| NDVI | (NIR − RED) / (NIR + RED) |
| SAVI | (1 + L)(NIR − RED) / (NIR + RED + L), L = 0,5 |
| MSAVI | (2·NIR + 1 − √((2·NIR + 1)² − 8(NIR − RED))) / 2 |
| EVI | 2,5(NIR − RED) / (NIR + 6·RED − 7,5·BLUE + 1) |

con NIR = B8, RED = B4, BLUE = B2.

**Biomasa aérea.** Producto de referencia `NASA/ORNL/biomass_carbon_density/v1` (banda `agb`, Mg/ha, ≈2010, 300 m). Es un producto **estático**: no varía con las fechas.

**Los 5 rangos** (editables en `config.py`, la leyenda se actualiza sola):

| Clase | NDVI | SAVI / MSAVI | EVI | Biomasa (Mg/ha) |
|---|---|---|---|---|
| 1 Muy baja | ≤ 0,10 | ≤ 0,10 | ≤ 0,10 | ≤ 25 |
| 2 Baja | 0,10 – 0,30 | 0,10 – 0,25 | 0,10 – 0,25 | 25 – 75 |
| 3 Moderada | 0,30 – 0,50 | 0,25 – 0,40 | 0,25 – 0,40 | 75 – 150 |
| 4 Alta | 0,50 – 0,70 | 0,40 – 0,55 | 0,40 – 0,60 | 150 – 250 |
| 5 Muy alta | > 0,70 | > 0,55 | > 0,60 | > 250 |

Cada rango incluye su límite superior. Los umbrales son una propuesta general; ajústelos si su estudio requiere otros cortes.

**Límite de Perú:** `USDOS/LSIB_SIMPLE/2017` (país «Peru»). Es una geometría simplificada, adecuada para visualización, no para fines legales o catastrales.

## 8. Limitaciones a tener en cuenta

- **Nubosidad:** la Amazonía y los Andes tienen nubes persistentes; con períodos cortos habrá huecos «sin datos». Use 6–12 meses o suba la nubosidad máxima.
- **Nieve y glaciares** se clasifican en el rango 1 (no se enmascaran).
- **Estadísticas** calculadas a 1 000 m (`STATS_SCALE_M`) para que respondan en tiempo razonable; son aproximadas.
- **Primera carga:** un composite sobre todo Perú puede tardar entre 10 y 60 s; los resultados se guardan en caché (6 h por defecto).
- **Período máximo:** 366 días. Fecha mínima: 2018-01-01.
- **Cuotas:** Earth Engine impone límites de uso por proyecto. Para mucho tráfico use una cuenta de servicio con cuota comercial.
- Verifique en el catálogo de Earth Engine que los IDs de datasets (`config`/`gee_service.py`) sigan vigentes: Google puede versionarlos o deprecarlos.

## 9. Pruebas

```bash
pip install -r requirements-dev.txt
pytest
```

Las pruebas cubren configuración, clasificación, validación de parámetros y manejo de errores sin necesitar credenciales de Earth Engine.

## 10. Solución de problemas

| Síntoma | Causa probable | Solución |
|---|---|---|
| Insignia roja «Earth Engine no disponible» | Credenciales o proyecto sin configurar | Revise `.env` y la sección 3 |
| Error de permisos | Proyecto no registrado en Earth Engine o cuenta de servicio no autorizada | Registre el proyecto / la cuenta de servicio |
| «No hay imágenes…» | Período muy corto o nubosidad muy baja | Amplíe fechas o aumente la nubosidad |
| «Tardó demasiado» | Período largo + cuota o carga alta | Reduzca el período y reintente |
| El mapa no aparece | Sin acceso a `unpkg.com`/`cdn.jsdelivr.net` | Permita esos dominios o aloje Leaflet 1.9.4 en `static/vendor/` |

## 11. Licencias de datos

Copernicus Sentinel-2 (ESA, libre) · NASA ORNL DAAC · USDOS LSIB · mapas base © OpenStreetMap, CARTO, Esri. Respete sus términos de atribución y uso.
