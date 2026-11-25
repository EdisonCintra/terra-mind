/* Busca de produto para o cadastro de insumo (F1 + E5): consulta o Agrofit pelo painel e preenche produto, tipo
   e bula. Se o produto ainda não tem bula na base, aceita o PDF e a indexa; só então o `bula_id` é preenchido
   (contrato 1: o nome do produto e o `bula_id` saem daqui, iguais aos da base de conhecimento). Todo texto vindo
   da rede entra por textContent: nada vira HTML. */
(function () {
  "use strict";

  const caixa = document.getElementById("busca-produto");
  if (!caixa) return;
  const entrada = document.getElementById("busca-q");
  const botao = document.getElementById("busca-ir");
  const saida = document.getElementById("busca-resultados");
  const produto = document.getElementById("campo-produto");
  const tipo = document.getElementById("campo-tipo");
  const bula = document.getElementById("campo-bula_id");

  function mensagem(texto, classe) {
    saida.replaceChildren();
    const p = document.createElement("p");
    p.className = classe || "nota";
    p.textContent = texto;
    saida.appendChild(p);
  }

  async function detalhe(resposta) {
    try { return (await resposta.json()).detail || "Erro " + resposta.status; } catch (e) { return "Erro " + resposta.status; }
  }

  async function buscar() {
    const q = entrada.value.trim();
    if (q.length < 3) { mensagem("Digite pelo menos 3 letras.", "mensagem-aviso"); return; }
    mensagem("Buscando…");
    let resposta;
    try { resposta = await fetch("/api/produtos/busca?q=" + encodeURIComponent(q)); }
    catch (e) { mensagem("Não consegui falar com o painel.", "mensagem-erro"); return; }
    if (!resposta.ok) {
      const motivo = await detalhe(resposta);
      mensagem(motivo === "agrofit_nao_configurado" ? "A busca no Agrofit não está configurada (AGROFIT_CLIENT_ID e AGROFIT_CLIENT_SECRET)." : motivo, "mensagem-erro");
      return;
    }
    mostrar(await resposta.json());
  }

  function escolher(p) {
    produto.value = p.produto;
    if (p.tipo_insumo) tipo.value = p.tipo_insumo;
    bula.value = p.bula_id || "";
    mensagem("Escolhido: " + p.produto + (p.bula_id ? " (bula " + p.bula_id + ")." : ". Sem bula na base: envie o PDF abaixo."), "mensagem-ok");
    if (!p.tipo_insumo) {
      const aviso = document.createElement("p");
      aviso.className = "mensagem-aviso";
      aviso.textContent = "A classe \"" + p.classe + "\" está fora do escopo (só fungicida, herbicida e inseticida): escolha o tipo à mão ou outro produto.";
      saida.appendChild(aviso);
    }
    if (!p.bula_id) enviarBula(p);
  }

  function enviarBula(p) {
    const bloco = document.createElement("div");
    bloco.className = "envio-bula";
    const rotulo = document.createElement("label");
    rotulo.textContent = "Bula em PDF de " + p.produto + " (reg. " + p.registro_mapa + ")";
    const arquivo = document.createElement("input");
    arquivo.type = "file";
    arquivo.accept = "application/pdf";
    const enviar = document.createElement("button");
    enviar.type = "button";
    enviar.textContent = "Enviar e indexar";
    const estado = document.createElement("span");
    estado.className = "nota";
    estado.setAttribute("aria-live", "polite");
    enviar.addEventListener("click", async function () {
      if (!arquivo.files.length) { estado.textContent = "Escolha o PDF."; return; }
      const dados = new FormData();
      dados.append("arquivo", arquivo.files[0]);
      enviar.disabled = true;
      estado.textContent = "Indexando a bula (pode levar um minuto)…";
      try {
        const r = await fetch("/api/produtos/" + encodeURIComponent(p.registro_mapa) + "/bula", { method: "POST", body: dados });
        if (!r.ok) { estado.textContent = await detalhe(r); enviar.disabled = false; return; }
        const corpo = await r.json();
        bula.value = corpo.bula_id;
        estado.textContent = "Bula indexada (" + corpo.trechos + " trechos). Pode salvar o insumo.";
      } catch (e) { estado.textContent = "Falha ao enviar."; enviar.disabled = false; }
    });
    bloco.append(rotulo, arquivo, enviar, estado);
    saida.appendChild(bloco);
  }

  function mostrar(lista) {
    saida.replaceChildren();
    if (!lista.length) { mensagem("Nenhum produto encontrado."); return; }
    const ul = document.createElement("ul");
    ul.className = "resultados-produto";
    lista.slice(0, 15).forEach(function (p) {
      const li = document.createElement("li");
      const b = document.createElement("button");
      b.type = "button";
      const nome = document.createElement("strong");
      nome.textContent = p.produto;
      const resto = document.createElement("small");
      resto.textContent = [p.classe, p.titular, p.bula_id ? "bula na base" : "sem bula na base"].filter(Boolean).join(" · ");
      b.append(nome, resto);
      b.addEventListener("click", function () { escolher(p); });
      li.appendChild(b);
      ul.appendChild(li);
    });
    saida.appendChild(ul);
  }

  botao.addEventListener("click", buscar);
  entrada.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); buscar(); } });
})();
