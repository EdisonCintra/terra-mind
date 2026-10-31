from typer.testing import CliRunner

from base_agricola import ingest


def rodar(monkeypatch, resultado):
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setattr(ingest, "QdrantClient", lambda url: None)
    monkeypatch.setattr(ingest, "indexar_fontes", lambda *a, **k: resultado)
    monkeypatch.setattr("base_agricola.db.criar_engine", lambda: None)
    monkeypatch.setattr("base_agricola.embeddings.FastEmbedEmbedder", lambda: None)
    return CliRunner().invoke(ingest.app, ["indexar"])


def test_indexar_termina_com_sucesso_se_todas_as_bulas_entraram(monkeypatch):
    r = rodar(monkeypatch, {"bula-a": 10, "bula-b": 20})
    assert r.exit_code == 0 and "bula-a: 10 trechos" in r.output


def test_indexar_falha_se_alguma_bula_nao_entrou(monkeypatch):
    # Na subida do compose, terminar "com sucesso" sem bula faria a busca voltar vazia.
    r = rodar(monkeypatch, {"bula-a": 10, "bula-b": "erro: qdrant fora do ar"})
    assert r.exit_code == 1 and "bula-b: erro" in r.output
