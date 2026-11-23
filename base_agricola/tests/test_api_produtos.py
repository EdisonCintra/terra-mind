import json
from pathlib import Path

import httpx
import pymupdf
import pytest
import respx
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sqlalchemy import text

from base_agricola.agrofit import URL_BUSCA, URL_TOKEN, ClienteAgrofit
from base_agricola.dependencias import get_agrofit, get_busca, get_repo
from base_agricola.embeddings import EmbedderDeterministico
from base_agricola.main import app
from base_agricola.repositorio import DOCS_PACOTE

FIXTURES = Path(__file__).parent / "fixtures"
CATALOGO = [json.loads((FIXTURES / f).read_text(encoding="utf-8"))[0]
            for f in ("agrofit_abacus_hc.json", "agrofit_brucia.json")]


def agrofit_simulado(req: httpx.Request) -> httpx.Response:
    """Filtra o catálogo (Abacus HC e Brucia) como a API: por registro ou por trecho da marca."""
    registro = req.url.params.get("numero_registro")
    marca = req.url.params.get("marca_comercial", "").lower()
    achados = [i for i in CATALOGO
               if (registro is None or i["numero_registro"] == registro) and marca in i["marca_comercial"][0].lower()]
    return httpx.Response(200, json=achados)


@pytest.fixture
def ambiente(repo, tmp_path, monkeypatch):
    """API com Agrofit simulado (respx), Qdrant em memória e BULAS_DIR temporária."""
    monkeypatch.setenv("BULAS_DIR", str(tmp_path))
    qdrant = QdrantClient(":memory:")
    with respx.mock:
        respx.post(URL_TOKEN).mock(return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 3500}))
        respx.get(URL_BUSCA).mock(side_effect=agrofit_simulado)
        app.dependency_overrides[get_agrofit] = lambda: ClienteAgrofit(httpx.Client(), "id", "segredo")
        app.dependency_overrides[get_busca] = lambda: (qdrant, EmbedderDeterministico(), "trechos")
        app.dependency_overrides[get_repo] = lambda: repo
        yield TestClient(app), repo, qdrant, tmp_path
    app.dependency_overrides.clear()


def pdf(nome: str = "abacus_hc.pdf") -> bytes:
    return (DOCS_PACOTE / nome).read_bytes()


def enviar(client, registro, conteudo):
    return client.post(f"/produtos/{registro}/bula", files={"arquivo": ("bula.pdf", conteudo, "application/pdf")})


def doc(repo, id):
    with repo.engine.connect() as conn:
        return conn.execute(text("SELECT * FROM conhecimento.documentos WHERE id = :id"), {"id": id}).mappings().first()


def test_busca_marca_produto_ja_indexado(ambiente):
    client, repo, _, _ = ambiente
    repo.upsert_documento("bula-brucia", "Bula Brucia", "bula", "Brucia", ["milho"], None, "brucia.pdf", "19123", "ISK")
    brucia = client.get("/produtos/busca", params={"q": "bru"}).json()
    assert [(p["registro_mapa"], p["bula_id"]) for p in brucia] == [("19123", "bula-brucia")]
    abacus = client.get("/produtos/busca", params={"q": "abacus"}).json()[0]
    assert abacus["bula_id"] is None and abacus["url_bula"].endswith(".pdf")


def test_busca_exige_tres_letras(ambiente):
    assert ambiente[0].get("/produtos/busca", params={"q": "ab"}).status_code == 422


def test_upload_indexa_e_registra(ambiente):
    client, repo, qdrant, pasta = ambiente
    resposta = enviar(client, "9210", pdf())
    corpo = resposta.json()
    assert resposta.status_code == 201 and corpo["bula_id"] == "bula-abacus-hc" and corpo["trechos"] > 0
    assert corpo["produto"] == "Abacus HC" and qdrant.count("trechos").count == corpo["trechos"]
    linha = doc(repo, "bula-abacus-hc")
    assert linha["registro_mapa"] == "9210" and linha["titular"] == "Basf S.A." and linha["indexado_em"]
    assert (pasta / linha["arquivo"]).read_bytes() == pdf() and "milho" in linha["culturas"]
    assert list(pasta.iterdir()) == [pasta / linha["arquivo"]]  # sem temporários sobrando
    # Mesmo registro de novo, com outro PDF: substitui a bula e reindexa.
    de_novo = enviar(client, "9210", pdf("ativum.pdf"))
    assert de_novo.status_code == 201
    textos = [p.payload["texto"] for p in qdrant.scroll("trechos", limit=1000)[0]]
    assert qdrant.count("trechos").count == de_novo.json()["trechos"] and not any("Abacus" in t for t in textos)
    assert (pasta / linha["arquivo"]).read_bytes() == pdf("ativum.pdf")


def test_registro_inexistente_404(ambiente):
    resposta = enviar(ambiente[0], "0", pdf())
    assert resposta.status_code == 404 and resposta.json() == {"detail": "registro_inexistente"}


def test_pdf_invalido_422_e_nao_apaga_nada(ambiente):
    client, repo, qdrant, pasta = ambiente
    assert enviar(client, "9210", pdf()).status_code == 201
    antes, arquivos = qdrant.count("trechos").count, {p.name: p.read_bytes() for p in pasta.iterdir()}
    resposta = enviar(client, "9210", b"isto nao e um pdf")
    assert resposta.status_code == 422 and resposta.json() == {"detail": "pdf_invalido"}
    assert qdrant.count("trechos").count == antes
    assert {p.name: p.read_bytes() for p in pasta.iterdir()} == arquivos
    assert doc(repo, "bula-abacus-hc")["arquivo"] in arquivos


def test_pdf_sem_texto_422_e_nao_apaga_nada(ambiente):
    client, _, qdrant, pasta = ambiente
    assert enviar(client, "9210", pdf()).status_code == 201
    antes, arquivos = qdrant.count("trechos").count, {p.name: p.read_bytes() for p in pasta.iterdir()}
    vazio = pymupdf.open()
    vazio.new_page()
    resposta = enviar(client, "9210", vazio.tobytes())
    assert resposta.status_code == 422 and resposta.json() == {"detail": "pdf_sem_texto"}
    assert qdrant.count("trechos").count == antes
    assert {p.name: p.read_bytes() for p in pasta.iterdir()} == arquivos


def test_arquivo_grande_demais_413_e_nao_apaga_nada(ambiente, monkeypatch):
    client, _, qdrant, pasta = ambiente
    assert enviar(client, "9210", pdf()).status_code == 201
    antes, arquivos = qdrant.count("trechos").count, {p.name: p.read_bytes() for p in pasta.iterdir()}
    monkeypatch.setattr("base_agricola.rotas_produtos.LIMITE_PDF", 1000)
    resposta = enviar(client, "9210", pdf())
    assert resposta.status_code == 413 and resposta.json() == {"detail": "arquivo_grande_demais"}
    assert qdrant.count("trechos").count == antes
    assert {p.name: p.read_bytes() for p in pasta.iterdir()} == arquivos


def test_produto_repetido_409(ambiente):
    client, repo, qdrant, pasta = ambiente
    assert enviar(client, "9210", pdf()).status_code == 201
    antes, arquivos = qdrant.count("trechos").count, {p.name: p.read_bytes() for p in pasta.iterdir()}
    # Outro registro (1111) passa a ser o dono do produto "Abacus HC".
    repo.upsert_documento("bula-abacus-hc", "Bula", "bula", "Abacus HC", ["soja"], None, "velho.pdf", "1111", "X")
    resposta = enviar(client, "9210", pdf("ativum.pdf"))
    assert resposta.status_code == 409
    assert resposta.json() == {"detail": "produto_ja_cadastrado", "bula_id": "bula-abacus-hc"}
    assert doc(repo, "bula-abacus-hc")["registro_mapa"] == "1111" and qdrant.count("trechos").count == antes
    assert {p.name: p.read_bytes() for p in pasta.iterdir()} == arquivos


def test_upload_adota_bula_antiga_sem_registro(ambiente):
    client, repo, _, _ = ambiente
    repo.upsert_documento("bula-abacus-hc", "Bula", "bula", "Abacus HC", ["soja"], None, "abacus_hc.pdf")
    assert enviar(client, "9210", pdf()).status_code == 201
    assert doc(repo, "bula-abacus-hc")["registro_mapa"] == "9210"


def test_agrofit_sem_credenciais_503(repo):
    app.dependency_overrides[get_agrofit] = lambda: None
    app.dependency_overrides[get_repo] = lambda: repo
    try:
        resposta = TestClient(app).get("/produtos/busca", params={"q": "abacus"})
    finally:
        app.dependency_overrides.clear()
    assert resposta.status_code == 503 and resposta.json() == {"detail": "agrofit_nao_configurado"}


def test_falha_do_embedder_no_reenvio_mantem_bula_antiga_e_503(ambiente):
    client, repo, qdrant, pasta = ambiente
    assert enviar(client, "9210", pdf()).status_code == 201
    pontos = sorted((p.id, p.payload["texto"]) for p in qdrant.scroll("trechos", limit=1000)[0])
    linha = dict(doc(repo, "bula-abacus-hc"))
    arquivos = {p.name: p.read_bytes() for p in pasta.iterdir()}

    class Quebrado(EmbedderDeterministico):
        def embed(self, textos):
            raise RuntimeError("embedder fora do ar")

    app.dependency_overrides[get_busca] = lambda: (qdrant, Quebrado(), "trechos")
    resposta = enviar(client, "9210", pdf("ativum.pdf"))
    assert resposta.status_code == 503 and resposta.json() == {"detail": "indexacao_falhou"}
    assert sorted((p.id, p.payload["texto"]) for p in qdrant.scroll("trechos", limit=1000)[0]) == pontos
    assert dict(doc(repo, "bula-abacus-hc")) == linha
    assert {p.name: p.read_bytes() for p in pasta.iterdir()} == arquivos


def test_marca_so_de_simbolos_usa_o_registro_no_id_e_no_arquivo(ambiente):
    client, repo, _, pasta = ambiente
    CATALOGO.append({**CATALOGO[1], "numero_registro": "777", "marca_comercial": ["***"]})
    try:
        resposta = enviar(client, "777", pdf())
    finally:
        CATALOGO.pop()
    assert resposta.status_code == 201 and resposta.json()["bula_id"] == "bula-777"
    assert doc(repo, "bula-777")["arquivo"] == "777.pdf" and (pasta / "777.pdf").is_file()
