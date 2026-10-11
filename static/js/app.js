/* ==========================================================================
   Geoportal de Índices de Vegetación · Perú — lógica del visor
   Funciona servido por Flask o como archivo HTML independiente
   (en ese caso se conecta a un servidor configurable).
   ========================================================================== */
(function () {
  "use strict";

  // ------------------------------------------------------------------ utils
  const $ = (sel, root = document) => root.querySelector(sel);

  function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
      if (value === false || value == null) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of [].concat(children)) {
      if (child == null) continue;
      node.append(child.nodeType ? child : document.createTextNode(child));
    }
    return node;
  }

  const swatch = (color) => {
    const node = el("span", { class: "swatch" });
    node.style.background = color;
    return node;
  };

  const strip = (classes) => {
    const node = el("span", { class: "strip", "aria-hidden": "true" });
    for (const c of classes) {
      const part = el("span");
      part.style.background = c.color;
      node.append(part);
    }
    return node;
  };

  const pad = (n) => String(n).padStart(2, "0");
  const toISO = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const daysBetween = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 86400000);
  const isMobile = () => window.matchMedia("(max-width: 900px)").matches;
  const abort = (ctl) => { if (ctl) ctl.abort(); };

  const fmtKm2 = new Intl.NumberFormat("es-PE", { maximumFractionDigits: 0 });
  const fmtPct = new Intl.NumberFormat("es-PE", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

  // ------------------------------------------------------------ constantes
  const FALLBACK = {
    js: "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
    css: "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css",
  };

  const BASEMAPS = {
    "Mapa claro": {
      url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      options: { subdomains: "abcd", maxZoom: 19, attribution: "&copy; OpenStreetMap &copy; CARTO" },
    },
    "Calles (OpenStreetMap)": {
      url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
      options: { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" },
    },
    "Satélite (Esri)": {
      url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      options: { maxZoom: 18, attribution: "Imágenes &copy; Esri, Maxar, Earthstar Geographics" },
    },
  };

  const IS_HTTP = /^https?:$/.test(window.location.protocol);
  const STORE_KEY = "geoportal.api";

  function readStore() { try { return window.localStorage.getItem(STORE_KEY) || ""; } catch (_) { return ""; } }
  function writeStore(value) {
    try { value ? window.localStorage.setItem(STORE_KEY, value) : window.localStorage.removeItem(STORE_KEY); } catch (_) { /* sin almacenamiento */ }
  }

  // Dirección del servidor: la inyectada al generar el HTML, la guardada por el
  // usuario o, si la página la sirve Flask, el mismo origen (cadena vacía).
  let apiBase = (readStore() || window.GEOPORTAL_API_BASE || "").replace(/\/+$/, "");

  const state = {
    config: null,
    map: null,
    indexKey: null,
    layer: null,
    boundary: null,
    baseLayer: null,
    legendEl: null,
    lastParams: null,
    seq: 0,
    tileErrors: 0,
    statusTimer: null,
    modalShown: false,
    ctl: { tiles: null, stats: null, pixel: null },
  };

  const currentIndex = () => state.config.indices.find((i) => i.key === state.indexKey);

  // --------------------------------------------------------------- Leaflet
  function ensureLeaflet() {
    if (window.L) return Promise.resolve();
    document.head.append(el("link", { rel: "stylesheet", href: FALLBACK.css }));
    return new Promise((resolve, reject) => {
      const script = el("script", { src: FALLBACK.js });
      script.onload = resolve;
      script.onerror = () => reject(new Error("No se pudo cargar la librería de mapas (Leaflet). Revise su conexión a Internet."));
      document.head.append(script);
    });
  }

  // ------------------------------------------------------------------- API
  function noServer(message) {
    const err = new Error(message);
    err.code = "NO_SERVER";
    return err;
  }

  async function api(path, params, signal, timeoutMs) {
    if (!IS_HTTP && !apiBase) throw noServer("No hay un servidor configurado.");
    const query = params ? "?" + new URLSearchParams(params).toString() : "";

    const ctl = new AbortController();
    const relay = () => ctl.abort();
    if (signal) signal.addEventListener("abort", relay);
    let timedOut = false;
    const timer = timeoutMs ? setTimeout(() => { timedOut = true; ctl.abort(); }, timeoutMs) : null;

    let res;
    try {
      res = await fetch(apiBase + path + query, { signal: ctl.signal, headers: { Accept: "application/json" } });
    } catch (err) {
      if (signal && signal.aborted) throw err; // cancelado a propósito
      throw noServer(timedOut ? "El servidor no respondió a tiempo." : "No se pudo contactar con el servidor.");
    } finally {
      if (timer) clearTimeout(timer);
      if (signal) signal.removeEventListener("abort", relay);
    }

    let data = null;
    try { data = await res.json(); } catch (_) { /* respuesta sin JSON */ }
    if (!res.ok) throw new Error((data && data.error) || `Error del servidor (${res.status}).`);
    return data;
  }

  // ---------------------------------------------------------------- estado UI
  function setStatus(message, kind = "info", busy = false) {
    const box = $("#status");
    clearTimeout(state.statusTimer);
    if (!message) { box.hidden = true; return; }
    $("#status-text").textContent = message;
    $("#status-spinner").hidden = !busy;
    box.dataset.kind = kind;
    box.hidden = false;
    if (kind === "ok") state.statusTimer = setTimeout(() => { box.hidden = true; }, 9000);
  }

  function setBusy(busy) {
    const btn = $("#apply");
    btn.disabled = busy;
    btn.textContent = busy ? "Procesando…" : "Visualizar en el mapa";
  }

  function setPill(kind, detail) {
    const pill = $("#gee-pill");
    const map = {
      wait: ["pill--wait", "Conectando…"],
      ok: ["pill--ok", "Earth Engine conectado"],
      nogee: ["pill--warn", "Servidor sin Earth Engine"],
      noserver: ["pill--error", "Sin servidor"],
    };
    const [cls, text] = map[kind];
    pill.className = "pill " + cls;
    $("#gee-pill-text").textContent = text;
    pill.title = detail || "Configurar servidor";
  }

  function setSheet(open) {
    $("#sidebar").classList.toggle("is-open", open);
    $("#sheet-toggle").setAttribute("aria-expanded", String(open));
  }

  // ------------------------------------------------------------- índices UI
  function renderIndexList() {
    const grid = $("#index-list");
    const tabs = $("#index-tabs");
    grid.replaceChildren();
    tabs.replaceChildren();
    for (const idx of state.config.indices) {
      const card = el(
        "button",
        { type: "button", class: "index-btn", role: "radio", "aria-checked": "false", dataset: { key: idx.key } },
        [
          el("span", { class: "index-btn__name", text: idx.name }),
          el("span", { class: "index-btn__sub", text: idx.short }),
          strip(idx.classes),
        ]
      );
      card.addEventListener("click", () => selectIndex(idx.key, true));
      grid.append(card);

      const tab = el(
        "button",
        { type: "button", class: "tab", role: "radio", "aria-checked": "false", dataset: { key: idx.key } },
        [el("span", { class: "tab__name", text: idx.name }), strip(idx.classes)]
      );
      tab.addEventListener("click", () => selectIndex(idx.key, true));
      tabs.append(tab);
    }
  }

  function renderIndexInfo() {
    const idx = currentIndex();
    $("#index-info").replaceChildren(
      el("strong", { text: idx.full_name }),
      el("div", { class: "info__formula", text: idx.formula }),
      el("p", { text: idx.note }),
      el("p", { class: "info__src", text: "Fuente: " + idx.source })
    );
  }

  function renderLegend() {
    const box = state.legendEl;
    if (!box) return;
    const idx = currentIndex();
    const title = idx.unit ? `${idx.name} (${idx.unit})` : idx.name;

    const head = el("button", { type: "button", class: "legend__head", "aria-expanded": String(!box.classList.contains("is-collapsed")) }, [
      el("span", { class: "legend__title", text: title }),
      el("span", { class: "legend__chev", "aria-hidden": "true" }),
    ]);
    head.addEventListener("click", () => {
      const collapsed = box.classList.toggle("is-collapsed");
      head.setAttribute("aria-expanded", String(!collapsed));
    });

    const rows = el("div", { class: "legend__rows" });
    for (const c of idx.classes.slice().reverse()) {
      rows.append(
        el("div", { class: "legend__row" }, [
          swatch(c.color),
          el("div", { class: "legend__text" }, [
            el("span", { class: "legend__range", text: c.range }),
            el("span", { class: "legend__label", text: c.label }),
          ]),
        ])
      );
    }
    box.replaceChildren(head, rows);
  }

  function selectIndex(key, load) {
    state.indexKey = key;
    for (const btn of document.querySelectorAll(".index-btn, .tab")) {
      const active = btn.dataset.key === key;
      btn.setAttribute("aria-checked", String(active));
      if (active && btn.classList.contains("tab")) btn.scrollIntoView({ block: "nearest", inline: "center" });
    }
    const idx = currentIndex();
    renderIndexInfo();
    renderLegend();
    $("#period-fields").disabled = !idx.temporal;
    $("#period-hint").textContent = idx.temporal
      ? ""
      : "La biomasa aérea es un producto estático de referencia (≈2010): no depende del período ni de la nubosidad.";
    if (load) loadLayer();
  }

  // ---------------------------------------------------------------- período
  function applyPreset(days) {
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - days);
    $("#start").value = toISO(start);
    $("#end").value = toISO(end);
    for (const chip of document.querySelectorAll(".chip")) {
      chip.classList.toggle("is-active", Number(chip.dataset.days) === days);
    }
  }

  function clearPresetMarks() {
    for (const chip of document.querySelectorAll(".chip")) chip.classList.remove("is-active");
  }

  function currentParams() {
    const idx = currentIndex();
    const params = { index: idx.key };
    if (idx.temporal) {
      params.start = $("#start").value;
      params.end = $("#end").value;
      params.cloud = $("#cloud").value;
    }
    return params;
  }

  function validateParams(p) {
    if (!currentIndex().temporal) return null;
    if (!p.start || !p.end) return "Seleccione las fechas inicial y final.";
    if (p.end < p.start) return "La fecha final debe ser igual o posterior a la inicial.";
    const max = state.config.defaults.max_range_days;
    if (daysBetween(p.start, p.end) > max) return `El período máximo es de ${max} días.`;
    return null;
  }

  // ------------------------------------------------------------------ capa
  const opacityValue = () => Number($("#opacity").value) / 100;

  function swapLayer(url) {
    const previous = state.layer;
    state.tileErrors = 0;
    const layer = L.tileLayer(url, { opacity: opacityValue(), maxZoom: 16, zIndex: 10, updateWhenIdle: true });
    layer.on("tileerror", () => {
      state.tileErrors += 1;
      if (state.tileErrors === 8) {
        setStatus("Algunas teselas no se pudieron cargar. Si continúa, pulse «Visualizar» de nuevo.", "warn");
      }
    });
    layer.once("load", () => { if (previous) state.map.removeLayer(previous); });
    layer.addTo(state.map);
    state.layer = layer;
  }

  function describeResult(data) {
    const idx = currentIndex();
    if (!idx.temporal) return `${idx.name}: producto de referencia (≈2010) cargado.`;
    const scenes = data.scenes ? ` · ${fmtKm2.format(data.scenes)} escenas Sentinel-2` : "";
    return `${idx.name} · ${data.period.start} → ${data.period.end}${scenes}`;
  }

  async function loadLayer() {
    const params = currentParams();
    const problem = validateParams(params);
    if (problem) { setStatus(problem, "error"); if (isMobile()) setSheet(true); return; }

    const token = ++state.seq;
    abort(state.ctl.tiles); abort(state.ctl.stats); abort(state.ctl.pixel);
    state.ctl.tiles = new AbortController();
    state.map.closePopup();
    if (isMobile()) setSheet(false);
    setBusy(true);
    setStatus("Procesando la composición en Google Earth Engine. Puede tardar unos segundos…", "info", true);

    try {
      const data = await api("/api/tiles", params, state.ctl.tiles.signal);
      if (token !== state.seq) return;
      swapLayer(data.tile_url);
      state.lastParams = params;
      setBusy(false);
      setPill("ok");
      setStatus(describeResult(data), "ok");
      loadStats(params, token);
    } catch (err) {
      if (err.name === "AbortError" || token !== state.seq) return;
      setBusy(false);
      if (err.code === "NO_SERVER") {
        setPill("noserver");
        setStatus("No hay conexión con el servidor del geoportal.", "warn");
        openModal();
      } else {
        setStatus(err.message, "error");
      }
    }
  }

  async function loadBoundary() {
    if (state.boundary) return;
    try {
      const feature = await api("/api/boundary", null, null, 60000);
      state.boundary = L.geoJSON(feature, {
        pane: "boundaryPane",
        interactive: false,
        style: { color: "#f4a300", weight: 2, fill: false, opacity: 0.95 },
      });
      if ($("#toggle-boundary").checked) state.boundary.addTo(state.map);
    } catch (err) {
      console.warn("No se pudo cargar el límite de Perú:", err.message);
    }
  }

  // ---------------------------------------------------------- estadísticas
  function renderStats(s) {
    const box = $("#stats");
    box.replaceChildren();
    for (const c of s.classes.slice().reverse()) {
      const fill = el("div", { class: "bar__fill" });
      fill.style.width = `${Math.min(c.percent, 100)}%`;
      fill.style.background = c.color;
      box.append(
        el("div", { class: "stat" }, [
          el("div", { class: "stat__head" }, [
            swatch(c.color),
            el("span", { class: "stat__label", text: `${c.label} (${c.range})` }),
            el("span", { class: "stat__pct", text: `${fmtPct.format(c.percent)} %` }),
          ]),
          el("div", { class: "bar" }, [fill]),
          el("div", { class: "stat__area", text: `${fmtKm2.format(c.area_km2)} km²` }),
        ])
      );
    }
    const summary = el("div", { class: "stats__summary" }, [
      el("span", {}, [
        "Área con datos: ",
        el("strong", { text: `${fmtKm2.format(s.valid_km2)} km²` }),
        ` (${fmtPct.format(s.coverage_percent)} % de Perú)`,
      ]),
    ]);
    if (s.nodata_km2 > 0) {
      summary.append(el("span", { text: `Sin datos (nubes o sin imágenes): ${fmtKm2.format(s.nodata_km2)} km²` }));
    }
    summary.append(el("span", { text: `Porcentajes sobre el área con datos · cálculo a ${fmtKm2.format(s.scale_m)} m de resolución.` }));
    box.append(summary);
  }

  async function loadStats(params, token) {
    const box = $("#stats");
    abort(state.ctl.stats);
    state.ctl.stats = new AbortController();
    box.replaceChildren(el("p", { class: "muted", text: "Calculando área por rango…" }));
    try {
      const data = await api("/api/stats", params, state.ctl.stats.signal);
      if (token !== state.seq) return;
      renderStats(data);
    } catch (err) {
      if (err.name === "AbortError" || token !== state.seq) return;
      const retry = el("button", { type: "button", class: "btn btn--ghost btn--small", text: "Reintentar" });
      retry.addEventListener("click", () => loadStats(params, token));
      box.replaceChildren(el("p", { class: "muted", text: "No se pudieron calcular las estadísticas: " + err.message }), retry);
    }
  }

  // ------------------------------------------------------------ valor puntual
  function renderPixel(d) {
    const idx = currentIndex();
    const coords = `${d.lat.toFixed(4)}°, ${d.lon.toFixed(4)}°`;
    if (d.value == null) {
      return el("div", { class: "popup" }, [
        el("div", { class: "popup__title", text: idx.name }),
        el("div", { text: "Sin dato en este punto (fuera de Perú, nubes o sin imágenes)." }),
        el("div", { class: "popup__meta", text: coords }),
      ]);
    }
    const decimals = idx.unit ? 1 : 3;
    const value = d.value.toLocaleString("es-PE", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    return el("div", { class: "popup" }, [
      el("div", { class: "popup__title", text: idx.name }),
      el("div", { class: "popup__value", text: idx.unit ? `${value} ${idx.unit}` : value }),
      el("div", { class: "popup__class" }, [swatch(d.color), el("span", { text: `${d.label} (${d.range})` })]),
      el("div", { class: "popup__meta", text: coords }),
    ]);
  }

  async function onMapClick(e) {
    if (isMobile() && $("#sidebar").classList.contains("is-open")) { setSheet(false); return; }
    if (!state.layer || !state.lastParams) return;
    abort(state.ctl.pixel);
    state.ctl.pixel = new AbortController();
    const popup = L.popup({ maxWidth: 260 })
      .setLatLng(e.latlng)
      .setContent(el("div", { class: "popup", text: "Consultando valor…" }))
      .openOn(state.map);
    try {
      const data = await api(
        "/api/pixel",
        { ...state.lastParams, lat: e.latlng.lat.toFixed(5), lon: e.latlng.lng.toFixed(5) },
        state.ctl.pixel.signal
      );
      popup.setContent(renderPixel(data));
    } catch (err) {
      if (err.name === "AbortError") return;
      popup.setContent(el("div", { class: "popup popup--error", text: err.message }));
    }
  }

  // ------------------------------------------------------------------ mapa
  function setBasemap(name) {
    const def = BASEMAPS[name];
    if (state.baseLayer) state.map.removeLayer(state.baseLayer);
    state.baseLayer = L.tileLayer(def.url, { ...def.options, zIndex: 1 }).addTo(state.map);
  }

  function buildMap() {
    const view = state.config.view;
    const map = L.map("map", {
      center: view.center,
      zoom: view.zoom,
      minZoom: 4,
      maxZoom: 16,
      maxBounds: view.max_bounds,
      maxBoundsViscosity: 0.85,
      zoomSnap: 0.5,
    });
    state.map = map;

    const pane = map.createPane("boundaryPane");
    pane.style.zIndex = 450;
    pane.style.pointerEvents = "none";

    map.fitBounds(view.bounds, { padding: [10, 10] });
    L.control.scale({ metric: true, imperial: false, position: "bottomleft" }).addTo(map);

    const legend = L.control({ position: "topright" });
    legend.onAdd = () => {
      const div = L.DomUtil.create("div", "legend");
      L.DomEvent.disableClickPropagation(div);
      if (isMobile()) div.classList.add("is-collapsed");
      state.legendEl = div;
      return div;
    };
    legend.addTo(map);

    const coords = $("#coords");
    map.on("mousemove", (e) => { coords.textContent = `${e.latlng.lat.toFixed(4)}°, ${e.latlng.lng.toFixed(4)}°`; });
    map.on("mouseout", () => { coords.textContent = ""; });
    map.on("click", onMapClick);

    window.addEventListener("resize", () => map.invalidateSize());
  }

  // ------------------------------------------------------ servidor (ventana)
  function modalMessage(text, kind) {
    const msg = $("#server-msg");
    msg.textContent = text || "";
    msg.dataset.kind = kind || "";
  }

  function openModal() {
    state.modalShown = true;
    $("#server-url").value = apiBase;
    $("#server-reset").hidden = !IS_HTTP;
    modalMessage("", "");
    $("#server-modal").hidden = false;
    setTimeout(() => $("#server-url").focus(), 50);
  }

  function closeModal() { $("#server-modal").hidden = true; }

  async function onServerSubmit(event) {
    event.preventDefault();
    const url = $("#server-url").value.trim().replace(/\/+$/, "");
    if (!/^https?:\/\/[^\s/]+/i.test(url)) {
      modalMessage("Escriba una dirección completa que empiece con http:// o https://", "error");
      return;
    }
    if (window.location.protocol === "https:" && url.startsWith("http://")) {
      modalMessage("Esta página es segura (https) y el navegador bloquea servidores http. Use una dirección https.", "error");
      return;
    }
    modalMessage("Probando conexión…", "");
    try {
      const res = await fetch(url + "/api/health", { headers: { Accept: "application/json" } });
      const health = await res.json();
      if (!res.ok || health.status !== "ok") throw new Error("respuesta inesperada");
    } catch (_) {
      modalMessage("No se pudo conectar. Verifique la dirección, que el servidor esté en ejecución y que permita el acceso (CORS).", "error");
      return;
    }
    apiBase = url;
    writeStore(url);
    modalMessage("Conectado.", "ok");
    setTimeout(closeModal, 400);
    connect();
  }

  function onServerReset() {
    apiBase = "";
    writeStore("");
    closeModal();
    connect();
  }

  // --------------------------------------------------------------- controles
  function bindControls() {
    const select = $("#basemap");
    for (const name of Object.keys(BASEMAPS)) select.append(el("option", { value: name, text: name }));
    select.addEventListener("change", () => setBasemap(select.value));
    setBasemap(select.value);

    $("#opacity").addEventListener("input", (e) => {
      $("#opacity-out").textContent = `${e.target.value} %`;
      if (state.layer) state.layer.setOpacity(opacityValue());
    });
    $("#cloud").addEventListener("input", (e) => { $("#cloud-out").textContent = `${e.target.value} %`; });

    $("#toggle-boundary").addEventListener("change", (e) => {
      if (!state.boundary) return;
      if (e.target.checked) state.boundary.addTo(state.map);
      else state.map.removeLayer(state.boundary);
    });

    $("#reset-view").addEventListener("click", () => {
      state.map.fitBounds(state.config.view.bounds, { padding: [10, 10] });
      if (isMobile()) setSheet(false);
    });

    for (const chip of document.querySelectorAll(".chip")) {
      chip.addEventListener("click", () => applyPreset(Number(chip.dataset.days)));
    }
    $("#start").addEventListener("change", clearPresetMarks);
    $("#end").addEventListener("change", clearPresetMarks);

    $("#apply").addEventListener("click", loadLayer);
    $("#sheet-toggle").addEventListener("click", () => setSheet(!$("#sidebar").classList.contains("is-open")));
    $("#status-close").addEventListener("click", () => setStatus(""));

    $("#gee-pill").addEventListener("click", openModal);
    $("#server-form").addEventListener("submit", onServerSubmit);
    $("#server-close").addEventListener("click", closeModal);
    $("#server-reset").addEventListener("click", onServerReset);
    $("#server-modal").addEventListener("click", (e) => { if (e.target.id === "server-modal") closeModal(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  }

  // ------------------------------------------------------------------ inicio
  async function loadConfig() {
    try {
      return await api("/api/config", null, null, 8000);
    } catch (err) {
      if (window.GEOPORTAL_CONFIG) return window.GEOPORTAL_CONFIG; // modo sin servidor
      throw err;
    }
  }

  async function connect() {
    setPill("wait");
    let health;
    try {
      health = await api("/api/health", null, null, 8000);
    } catch (err) {
      setPill("noserver");
      setStatus("No hay conexión con el servidor del geoportal. Toque «Sin servidor» para configurarlo.", "warn");
      if (!state.modalShown) openModal();
      return;
    }
    if (!health.gee_ready) {
      setPill("nogee", health.gee_error || "");
      setStatus("El servidor está activo, pero Google Earth Engine no está configurado. Revise las credenciales (README).", "error");
      return;
    }
    setPill("ok");
    loadBoundary();
    loadLayer();
  }

  async function init() {
    try {
      await ensureLeaflet();
      state.config = await loadConfig();
    } catch (err) {
      setPill("noserver");
      setStatus(err.message, "error");
      return;
    }

    $("#end").max = toISO(new Date());
    $("#cloud").value = state.config.defaults.cloud;
    $("#cloud-out").textContent = `${state.config.defaults.cloud} %`;

    buildMap();
    renderIndexList();
    bindControls();
    applyPreset(state.config.defaults.range_days);
    selectIndex(state.config.indices[0].key, false);
    connect();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
