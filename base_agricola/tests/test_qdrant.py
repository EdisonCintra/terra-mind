from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from base_agricola.embeddings import EmbedderDeterministico
from base_agricola.ingest import indexar_fontes
from base_agricola.manifesto import Fonte
from base_agricola.modelos import Trecho
from base_agricola.qdrant import indexar


def trecho(doc_id="bula-x", ordem=0, texto="Não aplicar com chuva em 4 horas."):
    return Trecho(doc_id=doc_id, produto="Fungicida X", tipo_doc="bula", culturas=["soja"],
                  pagina=1, secao="condicoes_climaticas", ordem=ordem, texto=texto)


@pytest.fixture
def client_mem():
    return QdrantClient(":memory:")


def test_embedder_deterministico_estavel_e_com_dimensao():
    emb = EmbedderDeterministico(dimensao=32)
    a, b = emb.embed(["chuva forte", "chuva forte"])
    assert a == b and len(a) == 32


def test_indexar_duas_vezes_nao_duplica(client_mem):
    trechos = [trecho(ordem=i) for i in range(3)]
    indexar(client_mem, EmbedderDeterministico(), trechos, "trechos")
    indexar(client_mem, EmbedderDeterministico(), trechos, "trechos")
    assert client_mem.count("trechos").count == 3


def test_reindexar_documento_menor_remove_sobras(client_mem):
    indexar(client_mem, EmbedderDeterministico(), [trecho(ordem=i) for i in range(3)], "trechos")
    indexar(client_mem, EmbedderDeterministico(), [trecho(ordem=0)], "trechos")
    assert client_mem.count("trechos").count == 1


def test_reindexar_um_documento_preserva_os_outros(client_mem):
    indexar(client_mem, EmbedderDeterministico(), [trecho("bula-a"), trecho("bula-b")], "trechos")
    indexar(client_mem, EmbedderDeterministico(), [trecho("bula-a")], "trechos")
    assert client_mem.count("trechos").count == 2


def test_payload_guarda_o_trecho_inteiro(client_mem):
    indexar(client_mem, EmbedderDeterministico(), [trecho()], "trechos")
    [ponto] = client_mem.scroll("trechos", limit=1)[0]
    assert Trecho(**ponto.payload) == trecho()


class RepoFalso:
    def __init__(self):
        self.documentos = []

    def upsert_documento(self, **campos):
        self.documentos.append(campos["id"])


def test_indexar_fontes_segue_quando_um_pdf_falha(client_mem, tmp_path):
    bom = Path(__file__).resolve().parents[1] / "dados" / "docs" / "abacus_hc.pdf"
    quebrado = tmp_path / "quebrado.pdf"
    quebrado.write_text("não é pdf")
    fontes = [Fonte(id="ruim", titulo="R", tipo_doc="bula", produto="R", arquivo=quebrado, url="x"),
              Fonte(id="bula-abacus-hc", titulo="A", tipo_doc="bula", produto="Abacus HC",
                    arquivo=bom, url="x")]
    repo = RepoFalso()
    resultado = indexar_fontes(fontes, repo, client_mem, EmbedderDeterministico(), "trechos")
    assert isinstance(resultado["ruim"], str) and resultado["bula-abacus-hc"] > 0
    assert repo.documentos == ["bula-abacus-hc"]
