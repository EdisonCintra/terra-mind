import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from base_agricola.modelos import FatorPerda


def fator(valor=0.3, fonte="estimativa: x"):
    return FatorPerda(tipo_insumo="fungicida", condicao="chuva", fator=valor, fonte_ou_estimativa=fonte)


def test_fator_inexistente_volta_none(repo):
    assert repo.fator_perda("fungicida", "chuva") is None


def test_upsert_fator_substitui(repo):
    repo.upsert_fator(fator(0.3, "estimativa: x"))
    repo.upsert_fator(fator(0.4, "estimativa: y"))
    salvo = repo.fator_perda("fungicida", "chuva")
    assert salvo.fator == 0.4 and salvo.fonte_ou_estimativa == "estimativa: y"


def test_condicao_fora_do_contrato_rejeitada_no_banco(repo, engine):
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO conhecimento.fatores_perda (tipo_insumo, condicao, fator, fonte_ou_estimativa) "
            "VALUES ('fungicida', 'lavagem', 0.3, 'estimativa: x')"
        ))


def test_fator_fora_da_faixa_rejeitado_no_banco(repo, engine):
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO conhecimento.fatores_perda (tipo_insumo, condicao, fator, fonte_ou_estimativa) "
            "VALUES ('fungicida', 'chuva', 1.5, 'estimativa: x')"
        ))


def test_documentos_registrados(repo):
    for titulo in ("Bula X", "Bula X rev."):
        repo.upsert_documento("bula-x", titulo, "bula", "Fungicida X", ["soja"], "https://x", "x.pdf")
    assert repo.ids_documentos() == {"bula-x"}


def sha(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def cadastrar(repo, id="bula-x", produto="Fungicida X", arquivo="x.pdf", tipo_doc="bula"):
    repo.upsert_documento(id, "Bula X", tipo_doc, produto, ["soja"], None, arquivo)


def test_migracao_014_cadastra_as_seis_bulas_e_fenologias(fontes_reais):
    assert {f.id for f in fontes_reais} == {
        "bula-abacus-hc", "bula-ativum", "bula-brucia", "bula-amplexus", "fenologia-milho", "fenologia-soja"}
    assert all(f.arquivo.is_file() for f in fontes_reais)


def test_documento_novo_vai_para_a_indexacao(repo, tmp_path):
    (tmp_path / "x.pdf").write_bytes(b"conteudo")
    cadastrar(repo)
    [f] = repo.documentos_a_indexar(tmp_path)
    assert f.id == "bula-x" and f.arquivo == tmp_path / "x.pdf" and f.produto == "Fungicida X"


def test_documento_indexado_e_inalterado_nao_volta(repo, tmp_path):
    (tmp_path / "x.pdf").write_bytes(b"conteudo")
    cadastrar(repo)
    repo.marcar_indexado("bula-x", sha(b"conteudo"))
    assert repo.documentos_a_indexar(tmp_path) == []


def test_pdf_alterado_volta_para_a_indexacao(repo, tmp_path):
    (tmp_path / "x.pdf").write_bytes(b"conteudo")
    cadastrar(repo)
    repo.marcar_indexado("bula-x", sha(b"conteudo"))
    (tmp_path / "x.pdf").write_bytes(b"conteudo novo")
    assert [f.id for f in repo.documentos_a_indexar(tmp_path)] == ["bula-x"]


def test_arquivo_vem_da_primeira_pasta_que_o_tem(repo, tmp_path):
    volume, pacote = tmp_path / "volume", tmp_path / "pacote"
    volume.mkdir(), pacote.mkdir()
    (pacote / "x.pdf").write_bytes(b"a")
    (pacote / "y.pdf").write_bytes(b"b")
    (volume / "y.pdf").write_bytes(b"c")
    cadastrar(repo, "bula-x", "X", "x.pdf")
    cadastrar(repo, "bula-y", "Y", "y.pdf")
    achados = {f.id: f.arquivo for f in repo.documentos_a_indexar([volume, pacote])}
    assert achados == {"bula-x": pacote / "x.pdf", "bula-y": volume / "y.pdf"}


def test_arquivo_ausente_continua_na_fila_para_o_erro_aparecer(repo, tmp_path):
    cadastrar(repo)
    [f] = repo.documentos_a_indexar(tmp_path)
    assert f.arquivo == tmp_path / "x.pdf" and not f.arquivo.exists()


def test_forcar_traz_tambem_os_ja_indexados(repo, tmp_path):
    (tmp_path / "x.pdf").write_bytes(b"conteudo")
    cadastrar(repo)
    repo.marcar_indexado("bula-x", sha(b"conteudo"))
    assert [f.id for f in repo.documentos_a_indexar(tmp_path, forcar=True)] == ["bula-x"]


def test_duas_bulas_do_mesmo_produto_rejeitadas_no_banco(repo):
    cadastrar(repo, "bula-x", "Fungicida X")
    with pytest.raises(IntegrityError):
        cadastrar(repo, "bula-x2", "Fungicida X")


def test_listar_bulas_filtra_por_id(repo):
    repo.upsert_documento("bula-l1", "Bula L1", "bula", "Prod L1", [], None, "l1.pdf")
    repo.upsert_documento("ref-l2", "Ref L2", "referencia", None, [], None, "l2.pdf")
    assert [b["id"] for b in repo.listar_bulas() if b["id"].endswith("l1")] == ["bula-l1"]
    assert repo.listar_bulas("bula-l1") == [{"id": "bula-l1", "produto": "Prod L1"}]
    assert repo.listar_bulas("ref-l2") == []
