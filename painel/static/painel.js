/* Só apresentação: deriva números do que as rotas /parcial já trazem. Se algo falhar, os blocos seguem funcionando. */
(function () {
  "use strict";

  const REFRESCO_PREVISAO_MS = 60000;  // o Monitor renova a previsão a cada ciclo; reler de minuto em minuto basta
  const FONTES = { "open-meteo": "previsão Open-Meteo", replay: "cenário simulado" };
  const LIMIAR_PADRAO = 20;  // LIMIAR_CHUVA_MM padrão do Monitor (contrato 17)
  const BLOCO_H = 6;
  const FMT_DIA = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", day: "2-digit", month: "2-digit" });
  const FMT_HORA = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", hour: "2-digit", hourCycle: "h23" });
  const SVG = "http://www.w3.org/2000/svg";
  const STATUS_INCIDENTE = {
    aberto: ["Aberto", "neutro"],
    aguardando_aprovacao: ["Aguardando o gerente", "espera"],
    aguardando_decisao_manual: ["Decisão manual", "alerta"],
    encerrado: ["Encerrado", "ok"],
  };
  let previsaoReal = null;  // {talhao, fonte, buscadaEm, horas: [{t: Date, chuva, vento}]} ou {talhao, vazio: true}
  let lendoPrevisao = null;  // talhão cuja previsão está sendo lida agora
  let ultimaLeitura = 0;
  const historico = {};  // id do incidente -> {impactos, executado, encerrado, propostas}
  let sincronizando = false;
  let fixado = new URLSearchParams(location.search).get("incidente");  // incidente escolhido à mão; null = seguir o mais recente
  fixado = fixado ? +fixado : null;
  let maiorVisto = null;
  let incidenteDoSeletor;  // incidente a que o seletor de talhão já obedeceu ("" = nenhum); vem do servidor em #previsao[data-incidente]

  const $ = (id) => document.getElementById(id);
  const texto = (id, valor) => { const el = $(id); if (el) el.textContent = valor; };
  const numero = (v, casas = 0) => v.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  const reais = (v) => "R$ " + numero(v, 2);
  /* Cor só para estado: o valor do KPI ganha a classe do tom (ou nenhuma, neutro). */
  const tom = (id, classe) => { const n = $(id); if (n) n.className = classe || ""; };
  const el = (tag, classe, conteudo) => {
    const n = document.createElement(tag);
    if (classe) n.className = classe;
    if (conteudo !== undefined) n.textContent = conteudo;
    return n;
  };

  /* Impactos gravados na auditoria pela Ação Preventiva: {os_id: {area_ha, dose_por_ha, preco_unitario, fator, perda_rs}}. */
  function lerEventos(raiz) {
    const dados = { impactos: {}, executado: false, encerrado: false, propostas: [] };
    raiz.querySelectorAll(".evento").forEach((ev) => {
      if (ev.dataset.proposta) {
        try { dados.propostas.push(JSON.parse(ev.dataset.proposta)); } catch (_) { /* proposta ilegível */ }
      }
      if (ev.dataset.acao === "executar_plano") dados.executado = true;
      if (ev.dataset.acao === "encerrar_incidente") dados.encerrado = true;
      if (!ev.dataset.depois) return;
      try {
        const depois = JSON.parse(ev.dataset.depois);
        (depois.impactos || []).forEach((i) => { dados.impactos[i.os_id] = i; });
      } catch (_) { /* evento sem impactos */ }
    });
    return dados;
  }

  const soma = (xs) => xs.reduce((a, b) => a + b, 0);
  const idsDoSeletor = () => [...document.querySelectorAll("#seletor a[data-id]")].map((a) => +a.dataset.id);
  const incidenteAtivo = () => document.querySelector("#seletor a.ativa[data-id]");

  /* A auditoria de cada incidente é a fonte do total; incidente encerrado não muda e não é lido de novo. */
  async function sincronizarHistorico() {
    if (sincronizando) return;
    const ids = idsDoSeletor();
    if (!ids.length) return;
    sincronizando = true;
    try {
      for (let id = 1; id <= Math.max(...ids); id += 1) {
        if (historico[id] && historico[id].encerrado) continue;
        const r = await fetch(`/parcial/timeline?incidente=${id}`);
        if (!r.ok) continue;
        const html = new DOMParser().parseFromString(await r.text(), "text/html");
        historico[id] = lerEventos(html);
      }
    } catch (_) { /* tenta de novo no próximo ciclo */ } finally {
      sincronizando = false;
    }
    seguro();
  }

  const somaPerdas = (dados) => Object.values(dados.impactos).reduce((t, i) => t + (i.perda_rs || 0), 0);

  /* G1: o total da safra é do servidor (agente.impactos, simulações fora; o mesmo número de /perda). O painel
     só relê o valor de tempos em tempos e o tom segue o que veio; o navegador não soma mais nada. */
  const REFRESCO_PERDA_MS = 30000;
  let ultimaPerda = 0;
  async function lerPerdaDaSafra() {
    if (!$("kpi-perda") || Date.now() - ultimaPerda < REFRESCO_PERDA_MS) return;
    ultimaPerda = Date.now();
    try {
      const r = await fetch("/api/perda-evitada");
      if (!r.ok) return;  // fica o valor que o servidor já mandou com a página
      const perda = await r.json();
      texto("kpi-perda", perda.total);
      tom("kpi-perda", perda.total_rs > 0 ? "bom" : "");
    } catch (_) { /* tenta de novo no próximo intervalo */ }
  }

  /* Linha de baixo do KPI: o incidente escolhido (inclusive simulação), lido da auditoria. */
  function atualizarPerda() {
    lerPerdaDaSafra();
    const ids = Object.keys(historico).map(Number).sort((a, b) => a - b);
    const ativo = incidenteAtivo();
    const escolhido = ativo ? +ativo.dataset.id : null;
    const comPerda = ids.filter((id) => Object.keys(historico[id].impactos).length);
    const id = comPerda.includes(escolhido) ? escolhido : comPerda[comPerda.length - 1];
    if (!id) {
      texto("kpi-perda-acao", "");
      return;
    }
    const situacao = historico[id].executado ? "executada" : historico[id].encerrado ? "sem plano executado" : "aguardando o gerente";
    const link = document.querySelector(`.seletor a[data-id="${id}"]`);
    const simulacao = link && link.dataset.origem === "replay" ? "simulação · " : "";  // o total acima não a inclui
    texto("kpi-perda-acao", `${simulacao}INC-${String(id).padStart(4, "0")}: ${reais(somaPerdas(historico[id]))} · ${situacao}`);
  }

  /* Primeiro dia inteiro sem chuva depois do período de chuva, no fuso da fazenda. */
  function proximoDiaSeco(prev, agora = new Date()) {
    const dia = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", day: "2-digit", month: "2-digit" });
    const hora = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", hour: "2-digit", hourCycle: "h23" });
    const iso = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Sao_Paulo", year: "numeric", month: "2-digit", day: "2-digit" });  // AAAA-MM-DD
    const tempo = (i) => new Date(prev.inicio.getTime() + i * 36e5);
    const inicioChuva = prev.chuva.findIndex((v) => v > 0);
    if (inicioChuva < 0) return { dia: dia.format(agora), fimChuva: null };
    let fim = inicioChuva;
    while (fim + 1 < prev.chuva.length && prev.chuva[fim + 1] > 0) fim += 1;
    const dias = new Map();
    prev.chuva.forEach((v, i) => {
      const chave = iso.format(tempo(i));
      const d = dias.get(chave) || { soma: 0, ultima: i };
      d.soma += v;
      d.ultima = i;
      dias.set(chave, d);
    });
    const fimChuva = `${dia.format(tempo(fim))} ${hora.format(tempo(fim))}h`;
    const hoje = iso.format(agora);  // datas do calendário de São Paulo comparam como texto
    for (const [chave, d] of dias) {
      if (d.ultima > fim && d.soma === 0 && chave >= hoje) return { dia: dia.format(tempo(d.ultima)), fimChuva };
    }
    return { dia: null, fimChuva };
  }

  function atualizarDiaSeco() {
    const prev = previsaoAtual();
    if (!prev) return;
    const seco = proximoDiaSeco(prev);
    texto("kpi-seco", seco.dia || "—");
    texto("kpi-seco-nota", seco.dia
      ? (seco.fimChuva ? `Chuva até ${seco.fimChuva} · ${rotuloFonte()}` : "Sem chuva prevista · " + rotuloFonte())
      : `Sem dia seco nos 5 dias · ${rotuloFonte()}`);
  }

  function atualizarOrdens(porOs) {
    let total = 0, suspensas = 0, reagendadas = 0;
    document.querySelectorAll("#ordens tr").forEach((tr) => {
      const status = tr.querySelector(".status");
      if (!status) return;
      total += 1;
      const suspensa = status.classList.contains("suspensa");
      const reagendada = status.classList.contains("reagendada");
      if (suspensa) suspensas += 1;
      if (reagendada) reagendadas += 1;
      const celula = tr.querySelector(".perda");
      const i = celula && porOs[celula.dataset.os];
      if ((suspensa || reagendada) && i && i.perda_rs != null) celula.textContent = reais(i.perda_rs);
    });
    texto("os-total", total ? `${total} ordens` : "");
    if (total) {
      texto("kpi-suspensas", String(suspensas));
      texto("kpi-suspensas-nota", `de ${total} · suspensão automática e reversível`);
      texto("kpi-reagendadas", String(reagendadas));
      texto("kpi-reagendadas-nota", `de ${total} · com plano aprovado`);
    }
    tom("kpi-suspensas", suspensas > 0 ? "atencao" : "");
    tom("kpi-reagendadas", reagendadas > 0 ? "azul" : "");
  }

  const rotuloFonte = () => (previsaoReal && previsaoReal.fonte) || "previsão";

  /* Talhão do gráfico = o do seletor único do bloco Previsão (que também manda nos cartões de clima).
     Sem seletor (nenhum talhão), cai no talhão padrão da página. */
  function talhaoDoGrafico() {
    const seletor = $("talhao-previsao");
    const bruto = seletor ? seletor.value : (($("previsao") || {}).dataset || {}).talhaoPadrao;
    const id = parseInt(bruto, 10);
    return isNaN(id) ? null : id;
  }

  const nomeDoTalhao = () => {
    const seletor = $("talhao-previsao");
    const opcao = seletor && seletor.selectedOptions[0];
    return opcao ? opcao.textContent : "";
  };

  /* Quando o incidente da linha do tempo muda (clique ou incidente novo), o seletor vai para o talhão dele.
     Depois disso o usuário pode trocar o talhão à vontade: a atualização de 2 s não desfaz a escolha. */
  function seguirTalhaoDoIncidente() {
    const ativo = document.querySelector("#seletor a.ativa[data-talhao]");
    const seletor = $("talhao-previsao");
    if (incidenteDoSeletor === undefined) {  // o servidor abriu o seletor no talhão deste incidente ("" = sem incidente)
      const previsao = $("previsao");
      incidenteDoSeletor = (previsao && previsao.dataset.incidente) || "";
    }
    if (!ativo || !seletor) return;
    if (ativo.dataset.id === incidenteDoSeletor) return;
    incidenteDoSeletor = ativo.dataset.id;
    if (seletor.value === ativo.dataset.talhao) return;
    if (![...seletor.options].some((o) => o.value === ativo.dataset.talhao)) return;
    seletor.value = ativo.dataset.talhao;
    seletor.dispatchEvent(new Event("change", { bubbles: true }));  // o HTMX recarrega os cartões
  }

  /* Lê a previsão guardada do talhão no Monitor (via painel). Falha ou sem previsão = estado vazio, nunca quebra o resto. */
  async function carregarPrevisao(talhao) {
    if (talhao === null || lendoPrevisao === talhao) return;
    lendoPrevisao = talhao;
    ultimaLeitura = Date.now();
    let novo = { talhao, vazio: true };
    try {
      const r = await fetch(`/api/previsao/${talhao}`);
      if (r.ok) {
        const horas = (await r.json()).map((h) => ({ t: new Date(h.ts), chuva: h.chuva_mm || 0, vento: h.vento_kmh || 0 }));
        const validas = horas.filter((h) => !isNaN(h.t));
        if (validas.length) {
          const cod = r.headers.get("X-Previsao-Fonte");
          novo = { talhao, horas: validas, fonte: FONTES[cod] || "previsão", buscadaEm: r.headers.get("X-Previsao-Buscada-Em") };
        }
      }
    } catch (_) { /* Monitor fora do ar: o gráfico mostra o estado vazio e tenta de novo depois */ } finally {
      lendoPrevisao = null;
    }
    previsaoReal = novo;
    seguro();
  }

  /* Série horária alinhada pelo ts de cada hora (buracos viram 0), não pelo início do incidente. */
  function previsaoAtual() {
    if (!$("grafico")) return null;  // só a Visão geral tem gráfico
    const talhao = talhaoDoGrafico();
    if (talhao === null) return null;
    if (!previsaoReal || previsaoReal.talhao !== talhao) {
      carregarPrevisao(talhao);
      return null;
    }
    if (previsaoReal.vazio) return null;
    const horas = previsaoReal.horas;
    const inicio = new Date();  // a janela começa na hora atual: a cópia guardada no navegador pode ter horas já passadas
    inicio.setMinutes(0, 0, 0);
    const futuras = horas.filter((h) => h.t >= inicio);
    if (!futuras.length) return null;
    const n = Math.min(120, Math.max(...futuras.map((h) => Math.round((h.t - inicio) / 36e5))) + 1);
    const chuva = new Array(n).fill(0), vento = new Array(n).fill(0);
    horas.forEach((h) => {
      const i = Math.round((h.t - inicio) / 36e5);
      if (i >= 0 && i < n) { chuva[i] = h.chuva; vento[i] = h.vento; }
    });
    return { inicio, chuva, vento, fonte: previsaoReal.fonte };
  }

  function desenharGrafico(prev, janela) {
    const caixa = $("grafico");
    const blocos = [];
    for (let i = 0; i < prev.chuva.length; i += BLOCO_H) {
      blocos.push(soma(prev.chuva.slice(i, i + BLOCO_H)));
    }
    const L = 760, A = 190, base = 154, topo = 16, passo = L / blocos.length;
    const maximo = Math.max(...blocos, 1);
    const altura = (v) => (v / maximo) * (base - topo);
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", `0 0 ${L} ${A}`);
    svg.setAttribute("role", "img");
    const total48 = soma(prev.chuva.slice(0, 48));
    const doTalhao = nomeDoTalhao() ? ` no ${nomeDoTalhao()}` : "";
    svg.setAttribute("aria-label", `Chuva a cada ${BLOCO_H} horas${doTalhao} em ${prev.fonte}: ${numero(total48)} mm nas primeiras 48 horas.`);
    const criar = (tag, attrs, conteudo) => {
      const n = document.createElementNS(SVG, tag);
      Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, v));
      if (conteudo !== undefined) n.textContent = conteudo;
      svg.append(n);
      return n;
    };
    criar("line", { x1: 0, x2: L, y1: base, y2: base, class: "eixo" });
    [0, 24, 48, 72, 96].forEach((h) => {
      const x = (h / BLOCO_H) * passo;
      criar("line", { x1: x, x2: x, y1: base, y2: base + 4, class: "eixo" });
      criar("text", { x, y: base + 20, class: "marca-eixo" }, h === 0 ? "início" : `+${h} h`);
    });
    if (janela) {  // início da última janela proposta, gravada na auditoria
      const horas = (janela - prev.inicio) / 36e5;
      if (horas >= 0 && horas <= 120) {
        const x = (horas / BLOCO_H) * passo;
        criar("line", { x1: x, x2: x, y1: topo - 4, y2: base, class: "janela" });
        const direita = x > L - 130;
        criar("text", { x: direita ? x - 6 : x + 6, y: topo + 4, class: "rotulo-janela", "text-anchor": direita ? "end" : "start" }, "janela proposta");
      }
    }
    const dica = el("div", "dica");
    dica.hidden = true;
    blocos.forEach((v, i) => {
      const h = v > 0 ? Math.max(altura(v), 3) : 0;
      const larg = Math.min(passo - 6, 16);
      const x = i * passo + (passo - larg) / 2;
      if (h) criar("rect", { x, y: base - h, width: larg, height: h, rx: 2, class: "barra" });
      const alvo = criar("rect", { x: i * passo, y: 0, width: passo, height: A, class: "alvo" });
      const de = new Date(prev.inicio.getTime() + i * BLOCO_H * 36e5);
      const fmt = (d) => `${FMT_DIA.format(d)} ${FMT_HORA.format(d)}h`;
      alvo.addEventListener("mouseenter", () => {
        dica.textContent = `${fmt(de)} · ${numero(v, 1)} mm em ${BLOCO_H} h`;
        dica.hidden = false;
        dica.style.left = `${Math.min(86, Math.max(14, ((i + 0.5) / blocos.length) * 100))}%`;
      });
      alvo.addEventListener("mouseleave", () => { dica.hidden = true; });
    });
    const nome = nomeDoTalhao();
    const legenda = el("div", "legenda-grafico", `Barras: chuva a cada ${BLOCO_H} h (mm)${nome ? " · " + nome : ""}`);
    caixa.replaceChildren(svg, dica, legenda);
  }

  function atualizarPrevisao(janela) {
    const prev = previsaoAtual();
    const caixa = $("grafico");
    if (!prev) {
      if (previsaoReal && previsaoReal.vazio && caixa && caixa.dataset.chave !== "vazio") {
        caixa.replaceChildren(el("p", "vazio", "Sem previsão guardada para este talhão ainda."));
        caixa.dataset.chave = "vazio";
        if ($("previsao-numeros")) $("previsao-numeros").hidden = true;
        texto("previsao-fonte", "previsão · 120 h");
        texto("kpi-chuva", "—");
        texto("kpi-chuva-nota", "Sem previsão para o talhão");
        tom("kpi-chuva", "");
        tom("prev-chuva", "");
      }
      return;
    }
    const chuva48 = soma(prev.chuva.slice(0, 48));
    const vento48 = Math.max(...prev.vento.slice(0, 48));
    const nome = nomeDoTalhao();
    const acima = chuva48 >= LIMIAR_PADRAO;  // vermelho só quando a chuva passa do limiar
    texto("kpi-chuva", `${numero(chuva48)} mm`);
    texto("kpi-chuva-nota", `${nome ? nome + " · " : ""}${acima ? "acima" : "abaixo"} do limiar de ${LIMIAR_PADRAO} mm · ${prev.fonte}`);
    tom("kpi-chuva", acima ? "quente" : "");
    tom("prev-chuva", acima ? "quente" : "");
    texto("prev-chuva", `${numero(chuva48)} mm`);
    texto("prev-vento", `${numero(vento48)} km/h`);
    texto("previsao-fonte", `${prev.fonte} · 120 h`);
    if ($("previsao-numeros")) $("previsao-numeros").hidden = false;
    if (!caixa) return;
    const chave = `${previsaoReal.talhao}|${nome}|${prev.inicio.getTime()}|${prev.fonte}|${chuva48}|${soma(prev.chuva)}|${janela ? janela.getTime() : ""}`;
    if (caixa.dataset.chave !== chave) {
      desenharGrafico(prev, janela);
      caixa.dataset.chave = chave;
    }
  }

  /* Sem o bloco de decisão, o estado do incidente fica no topo da linha do tempo. */
  function atualizarStatus() {
    const ativo = document.querySelector("#seletor a.ativa[data-status]");
    const selo = $("status-incidente");
    if (!ativo || !selo) return;
    const [rotulo, classe] = STATUS_INCIDENTE[ativo.dataset.status] || [ativo.dataset.status, "neutro"];
    selo.textContent = rotulo;
    selo.className = `selo ${classe}`;
    selo.hidden = false;
  }

  /* Seguir o incidente mais novo: sem escolha manual o servidor já devolve o último; com escolha, um incidente novo a desfaz. */
  const urls = () => {
    const q = fixado ? `?incidente=${fixado}` : "";
    return { eventos: `/parcial/timeline${q}`, seletor: `/parcial/seletor${q}` };
  };

  function aplicarSelecao() {
    const u = urls();
    $("eventos").setAttribute("hx-get", u.eventos);
    $("seletor").setAttribute("hx-get", u.seletor);
    if (window.htmx) {
      htmx.process($("eventos"));  // o htmx guarda o endereço ao processar: sem isto os ciclos seguem no antigo
      htmx.process($("seletor"));
      htmx.ajax("GET", u.eventos, { target: "#eventos", swap: "innerHTML" });
      htmx.ajax("GET", u.seletor, { target: "#seletor", swap: "innerHTML" });
    }
  }

  function seguirMaisRecente() {
    const ids = idsDoSeletor();
    if (!ids.length) return;
    const maior = Math.max(...ids);
    const novo = maiorVisto !== null && maior > maiorVisto;
    maiorVisto = Math.max(maiorVisto || 0, maior);
    if (novo) {
      fixado = null;
      aplicarSelecao();
    }
  }

  function atualizar() {
    if (!$("eventos")) return;  // painel.js vem em todas as telas; só a Visão geral tem linha do tempo
    seguirTalhaoDoIncidente();
    const atual = lerEventos($("eventos"));
    const ativo = incidenteAtivo();
    if (ativo) historico[+ativo.dataset.id] = atual;
    const porOs = {};
    Object.values(historico).forEach((h) => Object.assign(porOs, h.impactos));
    Object.assign(porOs, atual.impactos);
    const ultima = atual.propostas[atual.propostas.length - 1];
    const janela = ultima ? new Date(ultima.inicio) : null;
    atualizarOrdens(porOs);
    atualizarPerda();
    atualizarDiaSeco();
    atualizarPrevisao(janela && !isNaN(janela) ? janela : null);
    atualizarStatus();
  }

  function seguro() {
    try { atualizar(); } catch (erro) { console.error("painel.js", erro); }
  }

  document.body.addEventListener("htmx:afterSwap", (evento) => {
    seguro();
    if (evento.target.id === "seletor") {
      seguirMaisRecente();
      sincronizarHistorico();
    }
  });
  document.addEventListener("click", (evento) => {  // trocar de incidente sem recarregar a página
    const link = evento.target.closest("#seletor a[data-id]");
    if (!link) return;
    evento.preventDefault();
    fixado = +link.dataset.id === Math.max(...idsDoSeletor()) ? null : +link.dataset.id;
    aplicarSelecao();
  });
  document.addEventListener("change", (evento) => {  // seletor de talhão: o gráfico segue (os cartões vêm pelo HTMX)
    if (evento.target.id !== "talhao-previsao") return;
    texto("previsao-talhao", nomeDoTalhao());
    seguro();
  });
  /* Depois de "Simular chuva" o Monitor grava o cenário como previsão do talhão: reler já. */
  document.body.addEventListener("htmx:afterRequest", (evento) => {
    const caminho = evento.detail && evento.detail.pathInfo && evento.detail.pathInfo.requestPath;
    if (caminho === "/simular/chuva") { previsaoReal = null; seguro(); }
  });
  setInterval(() => {  // previsão nova a cada ciclo do Monitor; só onde há gráfico
    if (!$("grafico") || Date.now() - ultimaLeitura < REFRESCO_PREVISAO_MS) return;
    const talhao = talhaoDoGrafico();
    if (talhao !== null) carregarPrevisao(talhao);
  }, REFRESCO_PREVISAO_MS);
  seguro();
})();
