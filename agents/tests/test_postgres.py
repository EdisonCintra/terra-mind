"""Testes com Postgres de verdade: repositório e retomada da sessão após restart (o risco nº 1)."""

import json
from datetime import timedelta

import asyncpg
import pytest

from agents.clientes.repositorio import RepositorioPostgres
from agents.modelos import Resposta, como_json
from agents.runner import Orquestrador, criar_session_service
from tests.fakes import AGORA, incidente_chuva, montar_cenario

pytestmark = pytest.mark.pg


async def inserir_incidente(url: str) -> int:
    inc = incidente_chuva()
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetchval(
            "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)"
            " VALUES (1, $1::jsonb, $2::jsonb, 'aberto') RETURNING id",
            json.dumps(como_json(inc.gatilhos)),
            json.dumps(como_json(inc.horas)),
        )
    finally:
        await conn.close()


async def consultar(url: str, sql: str, *args):
    conn = await asyncpg.connect(url)
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def test_repositorio_incidente_e_status(pg_url):
    repo = RepositorioPostgres.de_url(pg_url)
    incidente_id = await inserir_incidente(pg_url)
    inc = await repo.carregar_incidente(incidente_id)
    assert inc.gatilhos[0].tipo == "chuva" and len(inc.horas) == 120
    assert await repo.mudar_status(incidente_id, "aguardando_aprovacao", de={"aberto"}) is True
    assert await repo.mudar_status(incidente_id, "encerrado", de={"aberto"}) is False
    assert await repo.carregar_incidente(9999) is None
    await repo.engine.dispose()


async def test_repositorio_resposta_idempotente(pg_url):
    repo = RepositorioPostgres.de_url(pg_url)
    incidente_id = await inserir_incidente(pg_url)
    sim = await repo.criar_plano(incidente_id, {"opcoes": [{"os_id": 1}]})
    nao = await repo.criar_plano(incidente_id, {"opcoes": []})
    assert await repo.registrar_resposta(sim, incidente_id, "SIM", "gerente") == "ok"
    assert await repo.registrar_resposta(sim, incidente_id, "SIM", "gerente") == "repetida"
    assert await repo.registrar_resposta(sim, incidente_id + 1, "SIM", "gerente") == "desconhecida"
    assert await repo.registrar_resposta(nao, incidente_id, "NAO", "gerente") == "ok"
    assert await repo.plano_aprovado(sim) is True and await repo.plano_aprovado(nao) is False
    linhas = {r["id"]: r for r in await consultar(pg_url, "SELECT * FROM agente.planos")}
    assert linhas[sim]["aprovado_por"] == "gerente" and linhas[sim]["opcao_escolhida"] == 0
    assert linhas[nao]["aprovado_por"] is None and linhas[nao]["respondido_em"] is not None
    await repo.engine.dispose()


async def test_repositorio_timeout_e_auditoria(pg_url):
    repo = RepositorioPostgres.de_url(pg_url)
    incidente_id = await inserir_incidente(pg_url)
    plano_id = await repo.criar_plano(incidente_id, {"opcoes": []})
    [linha] = await consultar(pg_url, "SELECT criado_em FROM agente.planos WHERE id = $1", plano_id)
    depois = linha["criado_em"] + timedelta(minutes=31)
    assert [p["plano_id"] for p in await repo.planos_para_escalar(depois, 30)] == [plano_id]
    await repo.marcar_escalado(plano_id, depois)
    assert await repo.planos_para_escalar(depois, 30) == []
    await repo.auditar(
        incidente_id, "teste", "acao", antes={"a": 1}, depois={"ts": AGORA}, justificativa="ok"
    )
    [aud] = await consultar(pg_url, "SELECT * FROM agente.auditoria")
    assert json.loads(aud["depois"])["ts"].startswith("2026-11-03")
    await repo.engine.dispose()


async def test_retoma_sessao_em_orquestrador_novo(pg_url):
    """Pausa na aprovação, "reinicia" o processo (runner, sessões e engine novos) e o SIM retoma."""
    incidente_id = await inserir_incidente(pg_url)
    c = montar_cenario()
    c.deps.repo = RepositorioPostgres.de_url(pg_url)
    c.erp.planos_aprovados = lambda plano_id: True  # aprovação no banco é coberta pelo teste acima

    primeiro = Orquestrador(c.deps, criar_session_service(pg_url))
    assert await primeiro.iniciar(incidente_id) is True
    [(inc, plano_id, _)] = c.canal.aprovacoes
    assert inc == incidente_id and c.erp.status(1) == "suspensa_preventiva"
    await c.deps.repo.engine.dispose()
    del primeiro

    c.deps.repo = RepositorioPostgres.de_url(pg_url)
    segundo = Orquestrador(c.deps, criar_session_service(pg_url))
    assert await segundo.responder(Resposta(incidente_id, plano_id, "SIM", "gerente"), esperar=True) == "ok"

    assert c.erp.status(1) == "reagendada"
    [status] = await consultar(pg_url, "SELECT status FROM agente.incidentes WHERE id = $1", incidente_id)
    assert status["status"] == "encerrado"
    tabelas_adk = await consultar(
        pg_url, "SELECT table_name FROM information_schema.tables WHERE table_schema = 'agente'"
    )
    assert {"sessions", "events"} <= {t["table_name"] for t in tabelas_adk}  # sessões do ADK no schema agente
    await c.deps.repo.engine.dispose()
