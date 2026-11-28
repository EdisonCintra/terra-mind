/* Mapa de talhões. TerraMapa.criar(elemento, {tiles, zoom}) -> {talhoes(lista, aoClicar, etiqueta)}.
   lista: [{id, nome, lat, lon, geom: GeoJSON|null, cor}]; sem `geom` desenha um circleMarker no lat/lon.
   Reaproveitável (ex.: cadastro de talhões). Se o Leaflet não carregar, nada quebra: o mapa só fica vazio. */
(function () {
  "use strict";

  const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";  // única URL externa do painel
  const ATRIBUICAO = "© OpenStreetMap";
  const REFRESCO_MS = 60000;  // o risco muda a cada ciclo do Monitor; reler de minuto em minuto basta

  function criar(elemento, opcoes) {
    const L = window.L;
    opcoes = opcoes || {};
    if (!L || !elemento) return { talhoes: function () {} };
    const mapa = L.map(elemento, { zoomControl: !opcoes.zoom }).setView([-15.8, -47.9], 4);
    if (opcoes.zoom) L.control.zoom({ position: opcoes.zoom }).addTo(mapa);
    L.tileLayer(opcoes.tiles || TILES, { maxZoom: 19, attribution: ATRIBUICAO }).addTo(mapa);
    const camada = L.featureGroup().addTo(mapa);
    let primeiraVez = true;

    /* `etiqueta(t)` devolve um nó (ou null) para a etiqueta fixa do talhão; sem ela, só o nome ao passar o mouse. */
    function talhoes(lista, aoClicar, etiqueta) {
      try {
        camada.clearLayers();
        (lista || []).forEach(function (t) {
          const estilo = { color: t.cor, fillColor: t.cor, fillOpacity: 0.28, weight: 1.5 };
          const circulo = function (latlng) { return L.circleMarker(latlng, Object.assign({ radius: 14 }, estilo)); };
          let forma = null;
          if (t.geom) forma = L.geoJSON(t.geom, { style: function () { return estilo; }, pointToLayer: function (feature, latlng) { return circulo(latlng); } });
          else if (typeof t.lat === "number" && typeof t.lon === "number") forma = circulo([t.lat, t.lon]);
          if (!forma) return;
          const fixa = etiqueta ? etiqueta(t) : null;
          if (fixa) {
            forma.bindTooltip(fixa, { permanent: true, direction: "right", offset: [14, 0], className: "etiqueta-leaflet", interactive: true });
          } else {
            // o Leaflet põe texto em innerHTML; um nó com textContent evita que o nome do talhão vire HTML
            const rotulo = document.createElement("span");
            rotulo.textContent = String(t.nome || "");
            forma.bindTooltip(rotulo);
          }
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
  if (!el || !document.body.classList.contains("tela-mapa")) return;
  const dadosTalhoes = document.getElementById("talhoes");
  let lista = [];
  try { lista = JSON.parse(dadosTalhoes ? dadosTalhoes.textContent : "[]"); } catch (e) { lista = []; }
  const mapa = criar(el, { zoom: "bottomright" });
  const estilo = getComputedStyle(document.documentElement);
  const COR = { verde: "--bom", amarelo: "--atencao", vermelho: "--risco" };
  const SELO = { verde: ["Pode aplicar", "bom"], amarelo: ["Atenção", "atencao"], vermelho: ["Fora do limite", "risco"] };
  const cor = (nivel) => (estilo.getPropertyValue(COR[nivel] || "--suave").trim() || "#98a39c");
  const no = (tag, classe, texto) => {
    const n = document.createElement(tag);
    if (classe) n.className = classe;
    if (texto !== undefined && texto !== null) n.textContent = String(texto);
    return n;
  };

  /* Etiqueta do Figma: nome e área, selo do risco, motivo e as OS das próximas 48 h. Só textContent. */
  function etiqueta(t, r) {
    const e = t.etiqueta || {};
    const nivel = r === undefined ? null : r ? r.nivel : "sem";
    const selo = nivel ? SELO[nivel] || ["Sem previsão", "neutro"] : [e.selo || "Sem previsão", e.tom || "neutro"];
    const motivo = r ? r.motivo : e.motivo;
    const caixa = no("div", "etiqueta-talhao");
    const nome = no("div", "nome-etiqueta");
    nome.append(no("strong", "", t.nome), no("span", "", e.area || ""));
    caixa.append(nome, no("span", "selo aplicacao " + selo[1], selo[0]));
    // motivo e OS ficam num bloco que aparece com o mapa aproximado ou ao passar o mouse (talhões colados se cobririam)
    const detalhe = no("div", "detalhe-etiqueta");
    if (motivo && selo[1] !== "bom") detalhe.append(no("p", "motivo-etiqueta", motivo));
    (e.ordens || []).forEach(function (o) {
      const os = no("div", "os-etiqueta");
      os.append(no("small", "", "Ordem de serviço #" + o.id));
      const linha = no("div", "linha-os-etiqueta");
      linha.append(no("strong", "", o.produto || "—"), no("strong", "", o.quando));
      os.append(linha);
      if (o.maquina) os.append(no("small", "", o.maquina));
      if (o.situacao) os.append(no("span", "situacao-etiqueta", o.situacao));
      detalhe.append(os);
      if (o.incidente_id) {
        const link = no("a", "link-acao", "Abrir plano INC-" + String(o.incidente_id).padStart(4, "0"));
        link.href = "/incidente/" + o.incidente_id;
        detalhe.append(link);
      }
    });
    if (detalhe.childNodes.length) caixa.append(detalhe);
    return caixa;
  }

  function clicou(id) {
    const t = lista.find(function (x) { return x.id === id; });
    const titulo = document.getElementById("detalhe-titulo");
    const painel = document.getElementById("painel-detalhe");
    if (titulo && t) titulo.textContent = t.nome;
    if (painel) painel.hidden = false;
    if (window.htmx) window.htmx.ajax("GET", "/parcial/mapa/talhao/" + id, { target: "#detalhe", swap: "innerHTML" });
    // no celular o detalhe fica abaixo do mapa: rola até ele para o toque ter resposta
    if (titulo && window.matchMedia && window.matchMedia("(max-width: 720px)").matches) {
      titulo.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  /* Aviso sobre o mapa (mesma aparência de c.faixa("aviso", ...)); texto vazio apaga. */
  const aviso = document.getElementById("mapa-aviso");
  function avisar(textoAviso) {
    if (!aviso) return;
    if (!textoAviso) { aviso.replaceChildren(); return; }
    const faixa = no("div", "faixa mensagem-aviso", textoAviso);
    faixa.setAttribute("role", "status");
    aviso.replaceChildren(faixa);
  }

  /* Contagem do topo (fora do limite, atenção, liberados, sem previsão) a partir do risco lido. */
  function contar(por) {
    const ul = document.getElementById("mapa-contagem");
    if (!ul) return;
    const n = { vermelho: 0, amarelo: 0, verde: 0, sem: 0 };
    lista.forEach(function (t) { const r = por[t.id]; n[r && n[r.nivel] !== undefined ? r.nivel : "sem"] += 1; });
    const itens = [["vermelho", "risco", " fora do limite"], ["amarelo", "atencao", " em atenção"],
      ["verde", "bom", n.verde === 1 ? " liberado" : " liberados"], ["sem", "neutro", " sem previsão"]];
    ul.replaceChildren();
    itens.forEach(function (i) {
      if (!n[i[0]]) return;
      const li = no("li");
      li.append(no("span", "ponto " + i[1]), document.createTextNode(n[i[0]] + i[2]));
      ul.append(li);
    });
  }

  /* `risco` null = ainda sem resposta do Monitor: usa o que a página trouxe (cache do servidor). */
  function desenhar(risco) {
    const por = {};
    (risco || []).forEach(function (r) { por[r.talhao_id] = r; });
    const TOM_NIVEL = { bom: "verde", atencao: "amarelo", risco: "vermelho" };
    mapa.talhoes(
      lista.map(function (t) {
        const nivel = risco ? (por[t.id] || {}).nivel : t.etiqueta && TOM_NIVEL[t.etiqueta.tom];
        return Object.assign({}, t, { cor: cor(nivel) });
      }),
      clicou,
      function (t) { return etiqueta(t, risco ? por[t.id] || null : undefined); },
    );
    if (risco) contar(por);
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
      if (lista.length && semPrevisao === lista.length) {
        avisar("Ainda não há previsão guardada para os talhões: eles aparecem em cinza até o próximo ciclo do Monitor.");
      } else if (semPrevisao) {
        avisar(semPrevisao + " talhão(ões) sem previsão aparecem em cinza.");
      } else {
        avisar("");
      }
    } catch (e) {
      desenhar(null);
      avisar("Não consegui falar com o Monitor: o risco não pôde ser calculado agora. Os talhões aparecem em cinza e o mapa tenta de novo a cada minuto.");
    }
  }

  /* Vento agora no primeiro talhão (Figma: canto superior direito). A seta aponta para onde o vento vai. */
  const RUMOS = ["norte", "nordeste", "leste", "sudeste", "sul", "sudoeste", "oeste", "noroeste"];
  async function vento() {
    const talhao = el.dataset.talhaoPadrao;
    const caixa = document.getElementById("vento-agora");
    if (!talhao || !caixa) return;
    try {
      const r = await fetch("/api/previsao/" + encodeURIComponent(talhao));
      if (!r.ok) return;
      const horas = await r.json();
      const agora = Date.now();
      const h = (horas || []).find(function (x) { return new Date(x.ts).getTime() + 3600000 > agora; });
      if (!h || typeof h.vento_kmh !== "number") return;
      const dir = typeof h.vento_dir_graus === "number" ? h.vento_dir_graus : null;
      let texto = Math.round(h.vento_kmh) + " km/h";
      if (dir !== null) texto += ", vindo de " + RUMOS[Math.round((((dir % 360) + 360) % 360) / 45) % 8];
      document.getElementById("vento-texto").textContent = texto;
      document.getElementById("vento-hora").textContent = "Vento às " + new Date(h.ts).toLocaleTimeString("pt-BR", { hour: "2-digit", timeZone: "America/Sao_Paulo" }) + "h";
      const seta = caixa.querySelector(".icone");
      if (seta && dir !== null) seta.style.transform = "rotate(" + ((dir + 180) - 225) + "deg)";  // o ícone aponta para sudoeste (225°)
      caixa.hidden = false;
    } catch (e) { /* sem vento, sem cartão */ }
  }

  const PERTO = 16;  // fitBounds para em 15: a vista inicial mostra etiquetas compactas
  if (mapa.leaflet) {
    mapa.leaflet.on("zoomend", function () {
      el.parentElement.classList.toggle("perto", mapa.leaflet.getZoom() >= PERTO);
    });
  }
  desenhar(null);
  atualizar();
  vento();
  setInterval(atualizar, REFRESCO_MS);
})();
