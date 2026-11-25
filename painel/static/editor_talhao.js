/* Editor de talhão (F1, contrato 18): desenha um ponto ou um polígono no mapa e grava o GeoJSON no campo `geom`.
   Usa o mapa reaproveitável do E4 (`TerraMapa.criar`) e só o Leaflet, sem plugin de desenho: ponto = um clique;
   polígono = um clique por vértice e "Concluir polígono". O servidor não confia nisto: confere a forma e recalcula o
   centro e a área (painel/geo.py). Aqui a área é só a sugestão que preenche o campo "Área (ha)". */
(function () {
  "use strict";

  const raiz = document.getElementById("editor-talhao");
  const el = document.getElementById("mapa-editor");
  if (!raiz || !el || !window.L || !window.TerraMapa) return; /* sem mapa o formulário segue; o servidor pede o desenho */

  const L = window.L;
  const campo = document.getElementById("campo-geom");
  const info = document.getElementById("editor-info");
  const area = document.getElementById("campo-area_ha");
  const mapa = window.TerraMapa.criar(el, {}).leaflet;
  const camada = L.featureGroup().addTo(mapa);
  const estilo = getComputedStyle(document.documentElement);
  const cor = estilo.getPropertyValue("--bom").trim() || "#45cf8e";
  const RAIO_TERRA_M = 6371008.8;

  let modo = "ponto";
  let vertices = []; /* [lat, lon] */
  let fechado = false;
  let areaDigitada = !!(area && area.value); /* área vinda do banco ou digitada: não sobrescreve */

  if (area) area.addEventListener("input", function () { areaDigitada = area.value !== ""; });

  /* Mesma projeção equiretangular do servidor (painel/geo.py). */
  function areaHa(pontos) {
    if (pontos.length < 3) return 0;
    const lat0 = pontos.reduce(function (s, p) { return s + p[0]; }, 0) / pontos.length;
    const lon0 = pontos.reduce(function (s, p) { return s + p[1]; }, 0) / pontos.length;
    const k = Math.cos(lat0 * Math.PI / 180);
    const xy = pontos.map(function (p) {
      return [(p[1] - lon0) * Math.PI / 180 * RAIO_TERRA_M * k, (p[0] - lat0) * Math.PI / 180 * RAIO_TERRA_M];
    });
    let dobro = 0;
    for (let i = 0; i < xy.length; i++) {
      const j = (i + 1) % xy.length;
      dobro += xy[i][0] * xy[j][1] - xy[j][0] * xy[i][1];
    }
    return Math.abs(dobro) / 2 / 10000;
  }

  function geojson() {
    if (modo === "ponto" && vertices.length === 1) return { type: "Point", coordinates: [vertices[0][1], vertices[0][0]] };
    if (modo === "poligono" && fechado && vertices.length >= 3) {
      const anel = vertices.map(function (p) { return [p[1], p[0]]; });
      anel.push(anel[0]);
      return { type: "Polygon", coordinates: [anel] };
    }
    return null;
  }

  function formatar(n, casas) { return n.toFixed(casas).replace(".", ","); }

  function atualizar() {
    camada.clearLayers();
    const estiloForma = { color: cor, fillColor: cor, fillOpacity: 0.35, weight: 2 };
    if (modo === "ponto" && vertices.length) {
      L.circleMarker(vertices[0], Object.assign({ radius: 11 }, estiloForma)).addTo(camada);
    } else if (modo === "poligono" && vertices.length) {
      if (fechado) L.polygon(vertices, estiloForma).addTo(camada);
      else if (vertices.length > 1) L.polyline(vertices, { color: cor, weight: 2, dashArray: "6 4" }).addTo(camada);
      vertices.forEach(function (v) { L.circleMarker(v, { radius: 5, color: cor, fillColor: "#fff", fillOpacity: 1, weight: 2 }).addTo(camada); });
    }
    const g = geojson();
    campo.value = g ? JSON.stringify(g) : "";
    let texto;
    if (modo === "ponto") {
      texto = vertices.length ? "Ponto em " + formatar(vertices[0][0], 5) + ", " + formatar(vertices[0][1], 5) + ". Informe a área (ha)." : "Clique no mapa para marcar o ponto.";
    } else if (fechado) {
      const ha = areaHa(vertices);
      texto = "Polígono com " + vertices.length + " vértices: cerca de " + formatar(ha, 2) + " ha.";
      if (area && !areaDigitada) area.value = ha.toFixed(2);
    } else {
      texto = vertices.length ? vertices.length + " vértice(s). Clique para continuar ou em \"Concluir polígono\"." : "Clique em cada canto do talhão.";
    }
    info.textContent = texto;
    raiz.querySelectorAll("[data-modo]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.modo === modo)); });
    raiz.querySelector("[data-acao=concluir]").disabled = modo !== "poligono" || fechado || vertices.length < 3;
    raiz.querySelector("[data-acao=desfazer]").disabled = !vertices.length;
    raiz.querySelector("[data-acao=limpar]").disabled = !vertices.length;
  }

  mapa.on("click", function (e) {
    const ponto = [e.latlng.lat, e.latlng.lng];
    if (modo === "ponto") vertices = [ponto];
    else if (!fechado) vertices.push(ponto);
    atualizar();
  });

  raiz.addEventListener("click", function (evento) {
    const botao = evento.target.closest("button");
    if (!botao) return;
    if (botao.dataset.modo) {
      modo = botao.dataset.modo;
      vertices = [];
      fechado = false;
    } else if (botao.dataset.acao === "concluir" && vertices.length >= 3) {
      fechado = true;
    } else if (botao.dataset.acao === "desfazer") {
      if (fechado) fechado = false;
      else vertices.pop();
    } else if (botao.dataset.acao === "limpar") {
      vertices = [];
      fechado = false;
    }
    atualizar();
  });

  /* Geometria já salva (edição, ou volta de um erro de validação) e enquadramento do mapa. */
  let existente = null;
  try { existente = campo.value ? JSON.parse(campo.value) : null; } catch (e) { existente = null; }
  if (existente && existente.type === "Point") {
    modo = "ponto";
    vertices = [[existente.coordinates[1], existente.coordinates[0]]];
  } else if (existente && existente.type === "Polygon") {
    modo = "poligono";
    vertices = existente.coordinates[0].slice(0, -1).map(function (p) { return [p[1], p[0]]; });
    fechado = vertices.length >= 3;
  }
  atualizar();
  if (vertices.length) {
    mapa.fitBounds(L.latLngBounds(vertices).pad(0.5), { maxZoom: 17 });
  } else if (el.dataset.centro) {
    const c = el.dataset.centro.split(",").map(Number);
    if (c.length === 2 && c.every(Number.isFinite)) mapa.setView(c, 14);
  }
})();
