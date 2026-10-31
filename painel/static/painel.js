/* Só apresentação: deriva números do que as rotas /parcial já trazem. Se algo falhar, os blocos seguem funcionando. */
(function () {
  "use strict";

  const CENARIO = "/static/chuva_80mm.json";  // cópia de monitor/scenarios (um teste garante que é igual)
  const LIMIAR_PADRAO = 20;  // LIMIAR_CHUVA_MM padrão do Monitor (contrato 17)
  const BLOCO_H = 6;
  const SVG = "http://www.w3.org/2000/svg";
  const STATUS_INCIDENTE = {
    aberto: ["Aberto", "neutro"],
    aguardando_aprovacao: ["Aguardando o gerente", "espera"],
    aguardando_decisao_manual: ["Decisão manual", "alerta"],
    encerrado: ["Encerrado", "ok"],
  };
  let cenario = null;
  const historico = {};  // id do incidente -> {impactos, executado, encerrado, propostas}
  let sincronizando = false;
  let fixado = new URLSearchParams(location.search).get("incidente");  // incidente escolhido à mão; null = seguir o mais recente
  fixado = fixado ? +fixado : null;
  let maiorVisto = null;

  const $ = (id) => document.getElementById(id);
  const texto = (id, valor) => { const el = $(id); if (el) el.textContent = valor; };
  const numero = (v, casas = 0) => v.toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  const reais = (v) => "R$ " + numero(v, 2);
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

  /* A auditoria de cada incidente é a fonte do total; incidente encerrado não muda e não é lido de novo. */
  async function sincronizarHistorico() {
    if (sincronizando) return;
    const ids = [...document.querySelectorAll("#seletor a[data-id]")].map((a) => +a.dataset.id);
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

  function atualizarPerda() {
    const ids = Object.keys(historico).map(Number).sort((a, b) => a - b);
    const total = ids.filter((id) => historico[id].executado).reduce((t, id) => t + somaPerdas(historico[id]), 0);
    texto("kpi-perda", reais(total));
    const ativo = document.querySelector("#seletor a.ativa[data-id]");
    const escolhido = ativo ? +ativo.dataset.id : null;
    const comPerda = ids.filter((id) => Object.keys(historico[id].impactos).length);
    const id = comPerda.includes(escolhido) ? escolhido : comPerda[comPerda.length - 1];
    if (!id) {
      texto("kpi-perda-acao", "");
      return;
    }
    const situacao = historico[id].executado ? "executada" : historico[id].encerrado ? "sem plano executado" : "aguardando o gerente";
    texto("kpi-perda-acao", `INC-${String(id).padStart(4, "0")}: ${reais(somaPerdas(historico[id]))} · ${situacao}`);
  }

  /* Primeiro dia inteiro sem chuva depois do período de chuva, no fuso da fazenda. */
  function proximoDiaSeco(prev, agora = new Date()) {
    const dia = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", day: "2-digit", month: "2-digit" });
    const hora = new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", hour: "2-digit", hourCycle: "h23" });
    const tempo = (i) => new Date(prev.inicio.getTime() + i * 36e5);
    const inicioChuva = prev.chuva.findIndex((v) => v > 0);
    if (inicioChuva < 0) return { dia: dia.format(agora), fimChuva: null };
    let fim = inicioChuva;
    while (fim + 1 < prev.chuva.length && prev.chuva[fim + 1] > 0) fim += 1;
    const dias = new Map();
    prev.chuva.forEach((v, i) => {
      const chave = dia.format(tempo(i));
      const d = dias.get(chave) || { soma: 0, ultima: i };
      d.soma += v;
      d.ultima = i;
      dias.set(chave, d);
    });
    const fimChuva = `${dia.format(tempo(fim))} ${hora.format(tempo(fim))}h`;
    const hoje = new Date(agora);
    hoje.setHours(0, 0, 0, 0);
    for (const [chave, d] of dias) {
      if (d.ultima > fim && d.soma === 0 && tempo(d.ultima) >= hoje) return { dia: chave, fimChuva };
    }
    return { dia: null, fimChuva };
  }

  function atualizarDiaSeco() {
    const prev = previsaoDoIncidente();
    if (!prev) return;
    const seco = proximoDiaSeco(prev);
    texto("kpi-seco", seco.dia || "—");
    texto("kpi-seco-nota", seco.dia
      ? (seco.fimChuva ? `Chuva até ${seco.fimChuva} · cenário gravado` : "Sem chuva prevista · cenário gravado")
      : "Sem dia seco nos 5 dias · cenário gravado");
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
  }

  /* Cenário gravado, deslocado como o Monitor faz: a primeira hora vira a hora em que o incidente abriu. */
  function previsaoDoIncidente() {
    const ativo = document.querySelector("#seletor a.ativa[data-criado]");
    if (!ativo || !ativo.dataset.criado || !cenario) return null;
    const inicio = new Date(ativo.dataset.criado);
    if (isNaN(inicio)) return null;
    inicio.setMinutes(0, 0, 0);
    return {
      inicio,
      chuva: cenario.hourly.precipitation.map((v) => v || 0),
      vento: cenario.hourly.wind_speed_10m.map((v) => v || 0),
    };
  }

  function desenharGrafico(prev, janela) {
    const caixa = $("grafico");
    const blocos = [];
    for (let i = 0; i < prev.chuva.length; i += BLOCO_H) {
      blocos.push(prev.chuva.slice(i, i + BLOCO_H).reduce((a, b) => a + b, 0));
    }
    const L = 760, A = 190, base = 154, topo = 16, passo = L / blocos.length;
    const maximo = Math.max(...blocos, 1);
    const altura = (v) => (v / maximo) * (base - topo);
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", `0 0 ${L} ${A}`);
    svg.setAttribute("role", "img");
    const total48 = prev.chuva.slice(0, 48).reduce((a, b) => a + b, 0);
    svg.setAttribute("aria-label", `Chuva a cada ${BLOCO_H} horas no cenário gravado: ${numero(total48)} mm nas primeiras 48 horas.`);
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
      const fmt = (d) => `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}h`;
      alvo.addEventListener("mouseenter", () => {
        dica.textContent = `${fmt(de)} · ${numero(v, 1)} mm em ${BLOCO_H} h`;
        dica.hidden = false;
        dica.style.left = `${Math.min(86, Math.max(14, ((i + 0.5) / blocos.length) * 100))}%`;
      });
      alvo.addEventListener("mouseleave", () => { dica.hidden = true; });
    });
    const legenda = el("div", "legenda-grafico", `Barras: chuva a cada ${BLOCO_H} h (mm)`);
    caixa.replaceChildren(svg, dica, legenda);
  }

  function atualizarPrevisao(janela) {
    const prev = previsaoDoIncidente();
    if (!prev) return;
    const chuva48 = prev.chuva.slice(0, 48).reduce((a, b) => a + b, 0);
    const vento48 = Math.max(...prev.vento.slice(0, 48));
    texto("kpi-chuva", `${numero(chuva48)} mm`);
    texto("kpi-chuva-nota", `Cenário gravado · limiar padrão do Monitor ${LIMIAR_PADRAO} mm`);
    texto("prev-chuva", `${numero(chuva48)} mm`);
    texto("prev-vento", `${numero(vento48)} km/h`);
    $("previsao-numeros").hidden = false;
    const chave = `${prev.inicio.getTime()}|${janela ? janela.getTime() : ""}`;
    if ($("grafico").dataset.chave !== chave) {
      desenharGrafico(prev, janela);
      $("grafico").dataset.chave = chave;
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
    return { eventos: `/parcial/timeline${q}`, seletor: `/timeline${q}` };
  };

  function aplicarSelecao() {
    const u = urls();
    $("eventos").setAttribute("hx-get", u.eventos);
    $("seletor").setAttribute("hx-get", u.seletor);
    if (window.htmx) {
      htmx.process($("eventos"));  // o htmx guarda o endereço ao processar: sem isto os ciclos seguem no antigo
      htmx.process($("seletor"));
      htmx.ajax("GET", u.eventos, { target: "#eventos", swap: "innerHTML" });
      htmx.ajax("GET", u.seletor, { target: "#seletor", select: ".seletor", swap: "innerHTML" });
    }
  }

  const idsDoSeletor = () => [...document.querySelectorAll("#seletor a[data-id]")].map((a) => +a.dataset.id);

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
    const atual = lerEventos($("eventos"));
    const ativo = document.querySelector("#seletor a.ativa[data-id]");
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
  fetch(CENARIO).then((r) => (r.ok ? r.json() : null)).then((d) => { cenario = d; seguro(); }).catch(() => {});
  seguro();
})();
