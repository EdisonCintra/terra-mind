/* Só apresentação. As telas são montadas no servidor e o HTMX troca os blocos; aqui só se apressa a releitura
   depois de uma ação, para o gerente ver o resultado sem esperar o próximo ciclo. Se algo falhar, nada quebra. */
(function () {
  "use strict";

  /* Relê agora um bloco que o HTMX já atualiza por tempo (o mesmo endereço do hx-get dele). */
  const reler = (seletor) => {
    const alvo = document.querySelector(seletor);
    const url = alvo && alvo.getAttribute("hx-get");
    if (url && window.htmx) htmx.ajax("GET", url, { target: seletor, swap: "innerHTML" });
  };
  const relerPendentes = () => reler("#pendentes");

  document.body.addEventListener("htmx:afterRequest", (evento) => {
    const caminho = evento.detail && evento.detail.pathInfo && evento.detail.pathInfo.requestPath || "";
    if (!evento.detail.successful) return;
    if (caminho.startsWith("/aprovacao/")) {
      relerPendentes();
      reler("#eventos");
      // Na tela do incidente o estado (selo, botões) muda com a resposta: recarrega quando o agente já gravou.
      if (document.querySelector(".incidente")) setTimeout(() => location.reload(), 2500);
    }
    if (caminho === "/simular/chuva") {
      // O replay abre o incidente e os agentes levam alguns segundos até o plano: relê duas vezes.
      setTimeout(relerPendentes, 4000);
      setTimeout(() => location.reload(), 12000);
    }
  });
})();
