"""PainelChannel grava só o alerta na auditoria (T8.8) e os textos do T8.9."""

import psycopg

from channels.base import criar_canal
from channels.painel import PainelChannel
from tests.conftest import DATABASE_URL


def auditoria():
    with psycopg.connect(DATABASE_URL) as conn:
        return conn.execute(
            "SELECT incidente_id, no, acao, depois, justificativa FROM agente.auditoria ORDER BY id"
        ).fetchall()


def contagem(tabela):
    with psycopg.connect(DATABASE_URL) as conn:
        return conn.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]


async def test_alerta_vira_linha_de_auditoria():
    await criar_canal("painel", None).enviar_alerta("sem resposta, OS segue suspensa")
    assert auditoria() == [(None, "painel", "enviar_alerta", None, "sem resposta, OS segue suspensa")]


async def test_alerta_pode_ser_ligado_ao_incidente():
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        inc = conn.execute(
            "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot)"
            " VALUES (1, '[]', '[]') RETURNING id"
        ).fetchone()[0]
    await PainelChannel().enviar_alerta("decida", incidente_id=inc)
    assert auditoria()[0][0] == inc


async def test_notificar_operador_registra_o_chat():
    await PainelChannel().notificar_operador(99, "OS remarcada")
    assert auditoria()[0][2:4] == ("notificar_operador", {"chat_id": 99})


async def test_pedir_aprovacao_nao_cria_nem_altera_planos():
    await PainelChannel().pedir_aprovacao(1, 1, "plano")
    assert contagem("agente.planos") == 0 and contagem("agente.auditoria") == 0


async def test_alerta_do_agents_e_ligado_ao_incidente_pelo_texto():
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        inc = conn.execute(
            "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot)"
            " VALUES (1, '[]', '[]') RETURNING id"
        ).fetchone()[0]
    await PainelChannel().enviar_alerta(f"Incidente {inc}: OS 1 segue SUSPENSA; você recusou o plano.")
    assert auditoria()[0][0] == inc


async def test_texto_sem_incidente_continua_sem_ligacao():
    await PainelChannel().enviar_alerta("Falha interna sem número")
    assert auditoria()[0][0] is None
