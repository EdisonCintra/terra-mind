"""Achados da revisão de 30/10: Qdrant fora do ar → 503; modelo carregado na subida."""

from fastapi.testclient import TestClient

import base_agricola.main as main
from base_agricola.embeddings import EmbedderDeterministico
from base_agricola.main import app, get_busca


class QdrantForaDoAr:
    def collection_exists(self, nome):
        raise RuntimeError("connection refused")


def test_qdrant_fora_do_ar_responde_503():
    app.dependency_overrides[get_busca] = lambda: (QdrantForaDoAr(), EmbedderDeterministico(), "trechos")
    try:
        r = TestClient(app).post("/trechos/busca", json={"consulta": "chuva"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503 and r.json()["detail"] == "qdrant_indisponivel"


def test_modelo_carregado_na_subida_quando_ha_qdrant(monkeypatch):
    chamadas = []
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setattr(main, "get_busca", lambda: chamadas.append(1))
    with TestClient(app):
        pass
    assert chamadas == [1]


def test_sem_qdrant_configurado_nao_carrega_modelo(monkeypatch):
    chamadas = []
    monkeypatch.delenv("QDRANT_URL", raising=False)
    monkeypatch.setattr(main, "get_busca", lambda: chamadas.append(1))
    with TestClient(app):
        pass
    assert chamadas == []
