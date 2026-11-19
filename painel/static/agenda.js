/* Agendar aplicação (G3): confere a janela quando a data muda e preenche o início com a sugestão aceita. */
(function () {
  "use strict";

  const form = document.querySelector("#nova-os form");
  if (!form) return;
  const janela = document.getElementById("janela");
  const criar = document.getElementById("criar-os");
  const inicio = form.elements.inicio;

  function conferir() {
    criar.disabled = true;
    if (!inicio.value) return;
    const dados = new FormData();
    ["talhao_id", "insumo_id", "inicio", "duracao_horas"].forEach((campo) => dados.set(campo, form.elements[campo].value));
    htmx.ajax("POST", "/agenda/avaliar", { target: "#janela", swap: "innerHTML", values: Object.fromEntries(dados) });
  }

  form.querySelectorAll("[data-janela]").forEach((campo) => campo.addEventListener("change", conferir));

  /* O servidor diz se a janela serve; só então a OS pode ser criada. */
  janela.addEventListener("htmx:afterSwap", () => {
    const resultado = janela.querySelector("[data-serve]");
    criar.disabled = !resultado || resultado.dataset.serve !== "sim";
  });

  /* Aceitar uma sugestão preenche o campo e confere de novo (a máquina livre vem na resposta). */
  janela.addEventListener("click", (evento) => {
    const botao = evento.target.closest(".sugestao");
    if (!botao) return;
    inicio.value = botao.dataset.inicio;
    conferir();
  });

  document.body.addEventListener("os-criada", () => {
    criar.disabled = true;
    janela.innerHTML = '<p class="vazio">Escolha a data para conferir a janela.</p>';
    inicio.value = "";
  });
})();
