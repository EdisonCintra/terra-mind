import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient

from base_agricola.busca import buscar
from base_agricola.chunking import dividir_em_trechos
from base_agricola.embeddings import EmbedderDeterministico
from base_agricola.main import app, get_busca
from base_agricola.manifesto import carregar_manifesto
from base_agricola.modelos import Trecho
from base_agricola.pdf import extrair_paginas
from base_agricola.qdrant import indexar

DADOS = Path(__file__).resolve().parents[1] / "dados"


def trecho(produto, secao, texto, ordem=0):
    return Trecho(doc_id=f"bula-{produto}", produto=produto, tipo_doc="bula", culturas=["soja"],
                  pagina=3, secao=secao, ordem=ordem, texto=texto)


@pytest.fixture
def indice():
    client, emb = QdrantClient(":memory:"), EmbedderDeterministico()
    indexar(client, emb, [
        trecho("Fungicida X", "condicoes_climaticas", "chuva em quatro horas após a aplicação"),
        trecho("Fungicida X", "modo_aplicacao", "diluir em água", ordem=1),
        trecho("Herbicida Y", "condicoes_climaticas", "chuva em até 48 horas"),
    ], "trechos")
    return client, emb


def test_filtra_por_produto(indice):
    res = buscar(*indice, "trechos", "chuva", produto="Fungicida X")
    assert res and {t.produto for t in res} == {"Fungicida X"}


def test_filtra_por_secao(indice):
    res = buscar(*indice, "trechos", "chuva", secao="condicoes_climaticas")
    assert res and {t.secao for t in res} == {"condicoes_climaticas"}


def test_devolve_citacao(indice):
    [t] = buscar(*indice, "trechos", "chuva quatro horas", produto="Fungicida X", limite=1)
    assert (t.fonte_doc, t.pagina) == ("bula-Fungicida X", 3) and "quatro horas" in t.texto


@pytest.fixture
def client_busca(indice):
    app.dependency_overrides[get_busca] = lambda: (*indice, "trechos")
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_busca(client_busca):
    r = client_busca.post("/trechos/busca", json={"consulta": "chuva", "produto": "Fungicida X", "limite": 1})
    assert r.status_code == 200 and r.json()[0]["produto"] == "Fungicida X"


def test_limite_maior_que_10_rejeitado(client_busca):
    assert client_busca.post("/trechos/busca", json={"consulta": "chuva", "limite": 11}).status_code == 422


def test_sem_resultado_retorna_lista_vazia(client_busca):
    r = client_busca.post("/trechos/busca", json={"consulta": "chuva", "produto": "Inexistente"})
    assert r.status_code == 200 and r.json() == []


LIMITES = {"Abacus HC": 7, "Ativum": 6, "Brucia": 9, "Amplexus": 5}


@pytest.mark.skipif(not os.environ.get("RODAR_LENTOS"), reason="baixa o modelo de embeddings; RODAR_LENTOS=1")
@pytest.mark.parametrize("produto", LIMITES)
def test_aceitacao_bulas_reais_trecho_do_limite_no_top3(produto):
    from base_agricola.embeddings import FastEmbedEmbedder

    client, emb = QdrantClient(":memory:"), FastEmbedEmbedder()
    for f in carregar_manifesto(DADOS / "fontes.yaml"):
        indexar(client, emb, dividir_em_trechos(f, extrair_paginas(f.arquivo)), "trechos")
    top3 = buscar(client, emb, "trechos", "chuva após a aplicação", produto=produto, limite=3)
    assert any(t.pagina == LIMITES[produto] and "horas" in t.texto for t in top3), [t.pagina for t in top3]
