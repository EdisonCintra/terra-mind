import json

import httpx
import pytest
import respx

from agents.tools.conhecimento import buscar_trechos

URL = "http://conhecimento-teste:8010"


@pytest.fixture(autouse=True)
def url(monkeypatch):
    monkeypatch.setenv("CONHECIMENTO_URL", URL)


def trecho_json(pagina=7):
    return {"texto": "chuvas dentro de quatro (4) horas", "fonte_doc": "bula-abacus-hc", "pagina": pagina,
            "secao": "condicoes_climaticas", "produto": "Abacus HC", "score": 0.9}


@respx.mock
def test_devolve_trechos_com_citacao_sem_score():
    respx.post(f"{URL}/trechos/busca").respond(200, json=[trecho_json()])
    [t] = buscar_trechos("chuva após a aplicação", produto="Abacus HC")["trechos"]
    assert t == {"texto": "chuvas dentro de quatro (4) horas", "fonte_doc": "bula-abacus-hc", "pagina": 7,
                 "secao": "condicoes_climaticas", "produto": "Abacus HC"}


@respx.mock
def test_envia_so_filtros_informados():
    rota = respx.post(f"{URL}/trechos/busca").respond(200, json=[])
    buscar_trechos("chuva", produto="Abacus HC")
    assert json.loads(rota.calls[0].request.content) == {"consulta": "chuva", "produto": "Abacus HC"}


@respx.mock
def test_lista_vazia_quando_documento_nao_traz_o_limite():
    respx.post(f"{URL}/trechos/busca").respond(200, json=[])
    assert buscar_trechos("chuva", produto="Inexistente") == {"trechos": []}


@respx.mock
def test_servico_fora_do_ar():
    respx.post(f"{URL}/trechos/busca").mock(side_effect=httpx.ConnectError("x"))
    assert buscar_trechos("chuva") == {"trechos": [], "erro": "servico_conhecimento_indisponivel"}


@respx.mock
def test_erro_500_nao_levanta_excecao():
    respx.post(f"{URL}/trechos/busca").respond(503, json={"detail": "qdrant_indisponivel"})
    assert buscar_trechos("chuva")["erro"] == "servico_conhecimento_indisponivel"
