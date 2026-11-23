
import pytest
from qdrant_client import QdrantClient
from typer.testing import CliRunner

from base_agricola import ingest
from base_agricola.embeddings import EmbedderDeterministico
from base_agricola.modelos import TrechoEncontrado
from base_agricola.repositorio import DOCS_PACOTE


class RepoSemPendencias:
    def __init__(self, engine):
        pass

    def documentos_a_indexar(self, base, forcar=False):
        return []


def rodar(monkeypatch, resultado):
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setattr(ingest, "QdrantClient", lambda url: None)
    monkeypatch.setattr(ingest, "selecionar_fontes", lambda *a, **k: [])
    monkeypatch.setattr(ingest, "indexar_fontes", lambda *a, **k: resultado)
    monkeypatch.setattr("base_agricola.db.criar_engine", lambda: None)
    monkeypatch.setattr("base_agricola.repositorio.RepositorioConhecimento", RepoSemPendencias)
    monkeypatch.setattr("base_agricola.embeddings.FastEmbedEmbedder", lambda: None)
    return CliRunner().invoke(ingest.app, ["indexar"])


def test_indexar_termina_com_sucesso_se_todas_as_bulas_entraram(monkeypatch):
    r = rodar(monkeypatch, {"bula-a": 10, "bula-b": 20})
    assert r.exit_code == 0 and "bula-a: 10 trechos" in r.output


def test_indexar_falha_se_alguma_bula_nao_entrou(monkeypatch):
    # Na subida do compose, terminar "com sucesso" sem bula faria a busca voltar vazia.
    r = rodar(monkeypatch, {"bula-a": 10, "bula-b": "erro: qdrant fora do ar"})
    assert r.exit_code == 1 and "bula-b: erro" in r.output


class EmbedderContador(EmbedderDeterministico):
    def __init__(self):
        super().__init__()
        self.chamadas = 0

    def embed(self, textos):
        self.chamadas += 1
        return super().embed(textos)


@pytest.fixture
def cenario(repo, tmp_path):
    """Um documento cadastrado cujo PDF é cópia da bula do Abacus HC, em pasta temporária."""
    (tmp_path / "x.pdf").write_bytes((DOCS_PACOTE / "abacus_hc.pdf").read_bytes())
    repo.upsert_documento("bula-x", "Bula X", "bula", "Fungicida X", ["soja"], None, "x.pdf")
    return repo, tmp_path, QdrantClient(":memory:")


def rodar_incremental(repo, base, client, embedder):
    return ingest.indexar_fontes(repo.documentos_a_indexar(base), repo, client, embedder, "trechos")


def test_rodar_duas_vezes_nao_reindexa(cenario):
    repo, base, client = cenario
    emb = EmbedderContador()
    primeira = rodar_incremental(repo, base, client, emb)
    chamadas = emb.chamadas
    segunda = rodar_incremental(repo, base, client, emb)
    assert primeira["bula-x"] > 0 and segunda == {} and emb.chamadas == chamadas


def test_pdf_trocado_reindexa(cenario):
    repo, base, client = cenario
    emb = EmbedderDeterministico()
    n_antes = rodar_incremental(repo, base, client, emb)["bula-x"]
    (base / "x.pdf").write_bytes((DOCS_PACOTE / "ativum.pdf").read_bytes())
    n_depois = rodar_incremental(repo, base, client, emb)["bula-x"]
    textos = [p.payload["texto"] for p in client.scroll("trechos", limit=1000)[0]]
    assert client.count("trechos").count == n_depois and n_depois != n_antes
    assert not any("Abacus" in t for t in textos) and any("Ativum" in t or "ATIVUM" in t for t in textos)


def test_falha_num_documento_nao_marca_indexado(cenario):
    repo, base, client = cenario
    (base / "x.pdf").write_text("não é pdf")
    resultado = rodar_incremental(repo, base, client, EmbedderDeterministico())
    assert isinstance(resultado["bula-x"], str)
    assert [f.id for f in repo.documentos_a_indexar(base)] == ["bula-x"]


def test_bulas_dir_tem_prioridade_sobre_o_pacote(monkeypatch, tmp_path):
    monkeypatch.setenv("BULAS_DIR", str(tmp_path))
    assert ingest.pastas_de_bulas() == [tmp_path, DOCS_PACOTE]
    monkeypatch.delenv("BULAS_DIR")
    assert ingest.pastas_de_bulas() == [DOCS_PACOTE]


def test_pdf_sem_texto_nao_marca_nem_apaga_o_que_ja_estava_indexado(cenario):
    import pymupdf
    repo, base, client = cenario
    emb = EmbedderDeterministico()
    n = rodar_incremental(repo, base, client, emb)["bula-x"]
    vazio = pymupdf.open()
    vazio.new_page()
    vazio.save(base / "x.pdf")
    vazio.close()
    resultado = rodar_incremental(repo, base, client, emb)
    assert isinstance(resultado["bula-x"], str) and "sem texto" in resultado["bula-x"]
    assert client.count("trechos").count == n
    assert [f.id for f in repo.documentos_a_indexar(base)] == ["bula-x"]


def test_qdrant_recriado_reindexa_mesmo_com_sha_marcado(cenario, capsys, monkeypatch):
    repo, base, client = cenario
    monkeypatch.setenv("BULAS_DIR", str(base))
    rodar_incremental(repo, base, client, EmbedderDeterministico())
    assert repo.documentos_a_indexar(base) == []
    novo = QdrantClient(":memory:")  # Qdrant recriado, Postgres mantido
    fontes = ingest.selecionar_fontes(repo, novo, None, False, "trechos")
    assert [f.id for f in fontes] == ["bula-x"]
    assert "ausente ou vazia" in capsys.readouterr().out


def test_doc_implica_forcar(cenario, monkeypatch):
    repo, base, client = cenario
    monkeypatch.setenv("BULAS_DIR", str(base))
    rodar_incremental(repo, base, client, EmbedderDeterministico())
    assert [f.id for f in ingest.selecionar_fontes(repo, client, "bula-x", False, "trechos")] == ["bula-x"]
    assert ingest.selecionar_fontes(repo, client, None, False, "trechos") == []


class RepoBulas:
    extras: list = []

    def __init__(self, engine):
        pass

    def listar_bulas(self, doc=None):
        bulas = [{"id": "bula-a", "produto": "Produto A"}, {"id": "bula-b", "produto": "Produto B"}]
        bulas += self.extras
        return [b for b in bulas if doc is None or b["id"] == doc]


def conferir(monkeypatch, achados, *args):
    """achados: produto -> lista de TrechoEncontrado (busca falsa, sem Qdrant nem modelo)."""
    consultas = []

    def buscar_falso(client, embedder, colecao, consulta, produto=None, **kw):
        consultas.append((consulta, produto))
        return achados.get(produto, [])

    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setattr(ingest, "QdrantClient", lambda url: None)
    monkeypatch.setattr(ingest, "buscar", buscar_falso)
    monkeypatch.setattr("base_agricola.db.criar_engine", lambda: None)
    monkeypatch.setattr("base_agricola.repositorio.RepositorioConhecimento", RepoBulas)
    monkeypatch.setattr("base_agricola.embeddings.FastEmbedEmbedder", lambda: None)
    r = CliRunner().invoke(ingest.app, ["conferir-chuva", *args])
    return r, consultas


def trecho(doc, pagina, texto):
    return TrechoEncontrado(texto=texto, fonte_doc=doc, pagina=pagina, secao="condicoes_climaticas",
                            produto="x", score=0.9)


def test_conferir_chuva_aponta_bula_sem_trecho(monkeypatch):
    r, _ = conferir(monkeypatch, {"Produto A": [trecho("bula-a", 4, "Não aplicar com chuva.")]})
    assert r.exit_code == 1
    assert "bula-b · SEM TRECHO" in r.output and "bula-a · 4 · Não aplicar" in r.output


def test_conferir_chuva_sai_com_zero_se_todas_tem_trecho(monkeypatch):
    longo = "chuva " + "x" * 300
    r, consultas = conferir(monkeypatch, {"Produto A": [trecho("bula-a", 4, longo)],
                                          "Produto B": [trecho("bula-b", 7, "Aguardar 2 horas sem chuva.")]})
    assert r.exit_code == 0
    assert f"bula-a · 4 · chuva {'x' * 114}" in r.output and "x" * 115 not in r.output
    assert "bula-b · 7 · Aguardar 2 horas sem chuva." in r.output
    assert {p for _, p in consultas} == {"Produto A", "Produto B"}


def test_conferir_chuva_doc_desconhecido_avisa_e_sai_com_1(monkeypatch):
    r, consultas = conferir(monkeypatch, {}, "--doc", "nao-existe")
    assert r.exit_code == 1 and "nao-existe" in r.output and not consultas


def test_conferir_chuva_doc_limita_a_uma_bula(monkeypatch):
    r, consultas = conferir(monkeypatch, {"Produto B": [trecho("bula-b", 2, "Sem chuva por 4 horas.")]}, "--doc", "bula-b")
    assert r.exit_code == 0 and len(consultas) == 1 and "bula-a" not in r.output


def test_conferir_chuva_mantem_cada_bula_em_uma_linha(monkeypatch):
    r, _ = conferir(monkeypatch, {"Produto A": [trecho("bula-a", 4, "Não aplicar\ncom   chuva.\n\nAguardar")]},
                    "--doc", "bula-a")
    assert "bula-a · 4 · Não aplicar com chuva. Aguardar" in r.output.strip().splitlines()


def test_conferir_chuva_trecho_de_lavagem_de_embalagem_e_sem_trecho(monkeypatch):
    lavagem = trecho("bula-a", 12, "Tríplice lavagem: esvazie a embalagem, adicione água e agite por 30 segundos.")
    r, _ = conferir(monkeypatch, {"Produto A": [lavagem]}, "--doc", "bula-a")
    assert r.exit_code == 1 and "bula-a · SEM TRECHO" in r.output


def test_conferir_chuva_so_abrigo_de_chuva_e_sem_trecho(monkeypatch):
    estoque = trecho("bula-a", 3, "Armazenar em local seco, ao Abrigo  de\nchuva e longe de crianças.")
    r, _ = conferir(monkeypatch, {"Produto A": [estoque]}, "--doc", "bula-a")
    assert r.exit_code == 1 and "bula-a · SEM TRECHO" in r.output


def test_conferir_chuva_escolhe_o_primeiro_hit_que_fala_de_chuva(monkeypatch):
    achados = [trecho("bula-a", 12, "Lave a embalagem três vezes."),
               trecho("bula-a", 3, "Guardar ao abrigo de chuva."),
               trecho("bula-a", 9, "Chuvas ocorridas até quatro horas após a aplicação podem reduzir a eficácia.")]
    r, _ = conferir(monkeypatch, {"Produto A": achados}, "--doc", "bula-a")
    assert r.exit_code == 0 and "bula-a · 9 · Chuvas ocorridas até quatro horas após a aplicação" in r.output


def test_conferir_chuva_busca_varios_hits(monkeypatch):
    limites = []

    def buscar_falso(client, embedder, colecao, consulta, produto=None, limite=None, **kw):
        limites.append(limite)
        return []

    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setattr(ingest, "QdrantClient", lambda url: None)
    monkeypatch.setattr(ingest, "buscar", buscar_falso)
    monkeypatch.setattr("base_agricola.db.criar_engine", lambda: None)
    monkeypatch.setattr("base_agricola.repositorio.RepositorioConhecimento", RepoBulas)
    monkeypatch.setattr("base_agricola.embeddings.FastEmbedEmbedder", lambda: None)
    CliRunner().invoke(ingest.app, ["conferir-chuva", "--doc", "bula-a"])
    assert limites == [5]


@pytest.mark.parametrize("produto", [None, ""])
def test_conferir_chuva_bula_sem_produto_nao_busca(monkeypatch, produto):
    monkeypatch.setattr(RepoBulas, "extras", [{"id": "bula-z", "produto": produto}])
    r, consultas = conferir(monkeypatch, {None: [trecho("outra", 1, "falso positivo")]}, "--doc", "bula-z")
    assert r.exit_code == 1 and "bula-z · SEM TRECHO" in r.output and not consultas
