import json
from pathlib import Path

import httpx
import respx

from base_agricola.agrofit import URL_BUSCA, URL_TOKEN, ClienteAgrofit, parse_produto, slug

FIXTURES = Path(__file__).parent / "fixtures"


def item(nome: str) -> dict:
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))[0]


def test_parse_produto_agrofit():
    p = parse_produto(item("agrofit_abacus_hc.json"))
    assert (p.registro_mapa, p.produto, p.classe, p.titular) == ("9210", "Abacus HC", "fungicida", "Basf S.A.")
    assert "Milho" in p.culturas and len(p.culturas) == len(set(p.culturas))
    assert p.url_bula.endswith("ABACUS-HC_bula_rev11_27-09-24.pdf")


def test_parse_sem_bula_ou_sem_marca():
    bruto = {**item("agrofit_brucia.json"), "documento_cadastrado": []}
    assert parse_produto(bruto).url_bula is None
    assert parse_produto({**bruto, "marca_comercial": []}) is None


def test_slug():
    assert slug("Abacus HC") == "abacus-hc" and slug("Ação  Fúria!") == "acao-furia"


@respx.mock
def test_token_agrofit_renova_quando_expira():
    agora = [0.0]
    token = respx.post(URL_TOKEN).mock(side_effect=lambda req: httpx.Response(
        200, json={"access_token": f"t{token.call_count + 1}", "token_type": "Bearer", "expires_in": 3500}))
    busca = respx.get(URL_BUSCA).mock(return_value=httpx.Response(200, json=[item("agrofit_brucia.json")]))
    cliente = ClienteAgrofit(httpx.Client(), "id", "segredo", relogio=lambda: agora[0])

    cliente.buscar("Brucia")
    agora[0] = 3000  # ainda faltam 500 s: reaproveita
    cliente.buscar("Brucia")
    assert token.call_count == 1
    agora[0] = 3450  # faltam 50 s (< 60): renova
    cliente.buscar("Brucia")
    assert token.call_count == 2
    assert busca.calls.last.request.headers["authorization"] == "Bearer t2"
    assert token.calls.last.request.headers["authorization"].startswith("Basic ")
    assert busca.calls.last.request.url.params["marca_comercial"] == "Brucia"


@respx.mock
def test_obter_filtra_por_registro():
    respx.post(URL_TOKEN).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3500}))
    rota = respx.get(URL_BUSCA).mock(return_value=httpx.Response(200, json=[item("agrofit_brucia.json")]))
    cliente = ClienteAgrofit(httpx.Client(), "id", "segredo")
    assert cliente.obter("19123").produto == "Brucia"
    assert rota.calls.last.request.url.params["numero_registro"] == "19123"
    assert cliente.obter("999") is None
