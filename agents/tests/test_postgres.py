"""Testes com Postgres de verdade: repositório e retomada da sessão após restart (o risco nº 1)."""

import json
from datetime import timedelta

import asyncpg
import pytest

from agents.clientes.repositorio import RepositorioPostgres
from agents.modelos import como_json
from agents.runner import Orquestrador, criar_session_service
from agents.tests.fakes import AGORA, incidente_chuva, montar_cenario
from channels.base import Resposta

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
    plano_id = await repo.criar_plano(incidente_id, {"opcoes": [], "custos_atraso": [{"os_id": 1}]})
    [linha] = await consultar(pg_url, "SELECT criado_em FROM agente.planos WHERE id = $1", plano_id)
    depois = linha["criado_em"] + timedelta(minutes=31)
    [pendente] = await repo.planos_para_escalar(depois, 30)
    assert pendente["plano_id"] == plano_id
    assert pendente["opcoes_json"]["custos_atraso"] == [{"os_id": 1}]  # G5: o lembrete lê daqui
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


async def test_repositorio_impactos_limites_e_relatorios(pg_url):
    """Contratos 25 e 26 e o registro do G7 no Postgres de verdade (migration 020)."""
    from agents.modelos import LimiteProduto

    repo = RepositorioPostgres.de_url(pg_url)
    incidente_id = await inserir_incidente(pg_url)
    await consultar(
        pg_url,
        "INSERT INTO erp.ordens_servico (id, talhao_id, tipo_operacao, inicio_previsto)"
        " VALUES (1, 1, 'pulverizacao', now()) RETURNING id",
    )
    linha = {
        "os_id": 1,
        "talhao_id": 1,
        "produto": "Abacus HC",
        "condicao": "chuva",
        "valor_aplicacao_rs": 6660.0,
        "fator": 0.5,
        "fonte": "estimativa: chuva lava o produto",
        "perda_rs": 3330.0,
    }
    await repo.gravar_impactos(incidente_id, [linha])
    await repo.gravar_impactos(incidente_id, [{**linha, "perda_rs": 3000.0}])  # regravar atualiza
    await repo.situar_impactos(incidente_id, "recusada", de={"evitada"})  # situação errada: não muda
    plano_id = await repo.criar_plano(incidente_id, {"opcoes": []})
    await repo.situar_impactos(incidente_id, "evitada", plano_id=plano_id)
    [impacto] = await consultar(pg_url, "SELECT * FROM agente.impactos")
    assert (impacto["situacao"], impacto["plano_id"], float(impacto["perda_rs"])) == (
        "evitada",
        plano_id,
        3000.0,
    )

    chuva = LimiteProduto(
        parametro="chuva",
        limite=0,
        unidade="mm",
        janela_h=4,
        trecho="quatro (4) horas",
        fonte_doc="bula-x",
        pagina=7,
    )
    vento = LimiteProduto(
        parametro="vento",
        sentido="minimo",
        limite=3,
        trecho="ventos de 3 a 10 km/h",
        fonte_doc="bula-x",
        pagina=8,
    )
    assert await repo.limites_do_produto("Abacus HC") == []
    await repo.salvar_limites("Abacus HC", [chuva, vento])
    await repo.salvar_limites("Abacus HC", [chuva])  # reler a bula troca, não duplica
    [lido] = await repo.limites_do_produto("Abacus HC")
    assert (lido.parametro, lido.sentido, lido.janela_h, lido.pagina, lido.generico) == (
        "chuva",
        "maximo",
        4,
        7,
        False,
    )

    assert await repo.proxima_versao_relatorio(1) == 1
    registro = await repo.salvar_relatorio(1, 1, {"os_id": 1}, "abc", b"%PDF-1.7", True)
    assert registro["versao"] == 1 and await repo.proxima_versao_relatorio(1) == 2
    [guardado] = await consultar(pg_url, "SELECT pdf, conforme, sha256 FROM agente.relatorios_aplicacao")
    assert bytes(guardado["pdf"]) == b"%PDF-1.7" and guardado["conforme"] is True
    await repo.engine.dispose()
