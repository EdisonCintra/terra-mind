"""Ferramenta de busca nas bulas para os agentes do ADK (contrato 10, frente do Edison)."""

import os

import httpx

CAMPOS = ("texto", "fonte_doc", "pagina", "secao", "produto")


def buscar_trechos(consulta: str, produto: str | None = None, secao: str | None = None) -> dict:
    """Busca trechos das bulas dos defensivos agrícolas, com a fonte e a página para citar.

    Para achar o limite de chuva, vento, temperatura ou umidade de um produto, filtre só por
    `produto` (nome comercial exato, como em erp.insumos.produto) e descreva o que procura, por
    exemplo consulta="chuva após a aplicação". Não filtre por `secao` para limites: cada bula
    coloca o limite de chuva numa seção diferente.

    Args:
        consulta: o que procurar, em português (ex.: "chuva após a aplicação").
        produto: nome comercial do produto (ex.: "Abacus HC"). Recomendado.
        secao: opcional; uma de condicoes_climaticas, modo_aplicacao, intervalo_seguranca,
            limitacoes_uso, outra.

    Returns:
        {"trechos": [{"texto", "fonte_doc", "pagina", "secao", "produto"}]}, do mais ao menos
        relevante. Lista vazia significa que o documento não traz esse limite: nesse caso não há
        violação citável. Se o serviço estiver fora do ar, vem também "erro".
    """
    corpo = {"consulta": consulta} | {k: v for k, v in (("produto", produto), ("secao", secao)) if v}
    try:
        with httpx.Client(base_url=os.environ.get("CONHECIMENTO_URL", "http://conhecimento:8010"),
                          timeout=5) as http:
            resposta = http.post("/trechos/busca", json=corpo)
            resposta.raise_for_status()
    except httpx.HTTPError:
        return {"trechos": [], "erro": "servico_conhecimento_indisponivel"}
    return {"trechos": [{c: t[c] for c in CAMPOS} for t in resposta.json()]}
