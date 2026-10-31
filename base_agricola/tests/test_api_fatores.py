import pytest
from fastapi.testclient import TestClient

from base_agricola.main import app, get_repo
from base_agricola.modelos import FatorPerda


class RepoFalso:
    def __init__(self, fatores):
        self.fatores = fatores

    def fator_perda(self, tipo_insumo, condicao):
        return self.fatores.get((tipo_insumo, condicao))


@pytest.fixture
def client_com_fatores():
    def criar(fatores):
        app.dependency_overrides[get_repo] = lambda: RepoFalso(fatores)
        return TestClient(app)

    yield criar
    app.dependency_overrides.clear()


def fator_exemplo():
    return FatorPerda(tipo_insumo="fungicida", condicao="chuva", fator=0.3, fonte_ou_estimativa="bula-x:p.4")


def test_fator_encontrado(client_com_fatores):
    r = client_com_fatores({("fungicida", "chuva"): fator_exemplo()}).get(
        "/fatores-perda", params={"tipo_insumo": "fungicida", "condicao": "chuva"}
    )
    assert r.status_code == 200
    assert r.json() == {"tipo_insumo": "fungicida", "condicao": "chuva", "fator": 0.3,
                        "fonte_ou_estimativa": "bula-x:p.4"}


def test_fator_inexistente_404(client_com_fatores):
    r = client_com_fatores({}).get("/fatores-perda", params={"tipo_insumo": "fungicida", "condicao": "chuva"})
    assert r.status_code == 404 and r.json()["detail"] == "fator_nao_cadastrado"


def test_condicao_invalida_422(client_com_fatores):
    params = {"tipo_insumo": "fungicida", "condicao": "lavagem"}
    r = client_com_fatores({}).get("/fatores-perda", params=params)
    assert r.status_code == 422


def test_parametro_faltando_422(client_com_fatores):
    assert client_com_fatores({}).get("/fatores-perda", params={"condicao": "chuva"}).status_code == 422
