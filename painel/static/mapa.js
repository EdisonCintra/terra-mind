/* Mapa de talhões. TerraMapa.criar(elemento, {tiles}) -> {talhoes(lista, aoClicar)}.
   lista: [{id, nome, lat, lon, geom: GeoJSON|null, cor}]; sem `geom` desenha um circleMarker no lat/lon.
   Reaproveitável (ex.: cadastro de talhões). Se o Leaflet não carregar, nada quebra: o mapa só fica vazio. */
(function () {
  "use strict";

  const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";  // única URL externa do painel
  const ATRIBUICAO = "© OpenStreetMap";
  const REFRESCO_MS = 60000;  // o risco muda a cada ciclo do Monitor; reler de minuto em minuto basta

  function criar(elemento, opcoes) {
    const L = window.L;
    if (!L || !elemento) return { talhoes: function () {} };
    const mapa = L.map(elemento, { zoomControl: true }).setView([-15.8, -47.9], 4);
    L.tileLayer((opcoes && opcoes.tiles) || TILES, { maxZoom: 19, attribution: ATRIBUICAO }).addTo(mapa);
    const camada = L.featureGroup().addTo(mapa);
    let primeiraVez = true;

    function talhoes(lista, aoClicar) {
      try {
        camada.clearLayers();
        (lista || []).forEach(function (t) {
          const estilo = { color: t.cor, fillColor: t.cor, fillOpacity: 0.45, weight: 2 };
          const circulo = function (latlng) { return L.circleMarker(latlng, Object.assign({ radius: 14 }, estilo)); };
          let forma = null;
          if (t.geom) forma = L.geoJSON(t.geom, { style: function () { return estilo; }, pointToLayer: function (feature, latlng) { return circulo(latlng); } });
          else if (typeof t.lat === "number" && typeof t.lon === "number") forma = circulo([t.lat, t.lon]);
          if (!forma) return;
          // o Leaflet põe texto em innerHTML; um nó com textContent evita que o nome do talhão vire HTML
          const rotulo = document.createElement("span");
          rotulo.textContent = String(t.nome || "");
          forma.bindTooltip(rotulo);
          if (aoClicar) forma.on("click", function () { aoClicar(t.id); });
          forma.addTo(camada);
        });
        if (primeiraVez && camada.getLayers().length) {
          mapa.fitBounds(camada.getBounds().pad(0.3), { maxZoom: 15 });
          primeiraVez = false;
        }
      } catch (e) { /* o mapa é apoio: erro de desenho não derruba a página */ }
    }
    return { talhoes: talhoes, leaflet: mapa };
  }

  window.TerraMapa = { criar: criar };

  // --- tela /mapa ---------------------------------------------------------------------------
  const el = document.getElementById("mapa");
  if (!el) return;
  const dadosTalhoes = document.getElementById("talhoes");
  let lista = [];
  try { lista = JSON.parse(dadosTalhoes ? dadosTalhoes.textContent : "[]"); } catch (e) { lista = []; }
  const nota = document.getElementById("mapa-nota");
  const mapa = criar(el, {});
  const estilo = getComputedStyle(document.documentElement);
  const COR = { verde: "--bom", amarelo: "--atencao", vermelho: "--risco" };
  const cor = (nivel) => (estilo.getPropertyValue(COR[nivel] || "--suave").trim() || "#93a19b");

  function clicou(id) {
    const t = lista.find(function (x) { return x.id === id; });
    const titulo = document.getElementById("detalhe-titulo");
    if (titulo && t) titulo.textContent = t.nome;
    if (window.htmx) window.htmx.ajax("GET", "/parcial/mapa/talhao/" + id, { target: "#detalhe", swap: "innerHTML" });
    // no celular (grade empilhada) o detalhe fica abaixo do mapa: rola até ele para o toque ter resposta
    if (titulo && window.matchMedia && window.matchMedia("(max-width: 1100px)").matches) {
      titulo.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* Aviso acima do mapa (mesma aparência de c.faixa("aviso", ...)); texto vazio apaga. */
  const aviso = document.getElementById("mapa-aviso");
  function avisar(textoAviso) {
    if (!aviso) return;
    if (!textoAviso) { aviso.replaceChildren(); return; }
    const faixa = document.createElement("div");
    faixa.className = "faixa mensagem-aviso";
    faixa.setAttribute("role", "status");
    faixa.textContent = textoAviso;
    aviso.replaceChildren(faixa);
  }

  function desenhar(risco) {
    const por = {};
    (risco || []).forEach(function (r) { por[r.talhao_id] = r.nivel; });
    mapa.talhoes(lista.map(function (t) { return Object.assign({}, t, { cor: cor(por[t.id]) }); }), clicou);
  }

  async function atualizar() {
    const f = el.dataset.fazenda;
    try {
      const r = await fetch("/api/risco" + (f ? "?fazenda_id=" + encodeURIComponent(f) : ""));
      if (!r.ok) throw new Error(String(r.status));
      const risco = await r.json();
      desenhar(risco);
      const comRisco = new Set((risco || []).map(function (x) { return x.talhao_id; }));
      const semPrevisao = lista.filter(function (t) { return !comRisco.has(t.id); }).length;
      if (nota) nota.textContent = "";
      if (lista.length && semPrevisao === lista.length) {
        avisar("Ainda não há previsão guardada para os talhões: eles aparecem em cinza até o próximo ciclo do Monitor.");
      } else if (semPrevisao) {
        avisar(semPrevisao + " talhão(ões) sem previsão aparecem em cinza.");
      } else {
        avisar("");
      }
    } catch (e) {
      desenhar([]);
      if (nota) nota.textContent = "risco indisponível agora";
      avisar("Não consegui falar com o Monitor: o risco não pôde ser calculado agora. Os talhões aparecem em cinza e o mapa tenta de novo a cada minuto.");
    }
  }

  atualizar();
  setInterval(atualizar, REFRESCO_MS);
})();
