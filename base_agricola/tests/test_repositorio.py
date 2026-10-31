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
