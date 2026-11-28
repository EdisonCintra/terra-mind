"""Consultas do assistente contra o Postgres de verdade: o SQL roda no schema real e nunca vaza de fazenda."""

import json
from datetime import date, timedelta

import asyncpg
import pytest

from agents.clientes.consultas import ConsultasPostgres, Escopo
from agents.clientes.repositorio import RepositorioPostgres
from agents.tests.fakes import AGORA

pytestmark = pytest.mark.pg

MINHA, OUTRA = Escopo((1,)), Escopo((2,))


@pytest.fixture
async def consultas(pg_url):
    """Fazenda 1 (a do fixture, gerente com chat 111) e fazenda 2 (vizinha), cada uma com tudo um pouco."""
    conn = await asyncpg.connect(pg_url)
    try:
        await conn.execute(
            """
            INSERT INTO erp.fazendas (id, organizacao_id, nome) VALUES (2, 1, 'Fazenda Vizinha');
            INSERT INTO erp.talhoes (id, fazenda_id, nome, area_ha, cultura, data_plantio, estagio_fenologico,
                                     lat, lon)
                VALUES (2, 2, 'Talhão Vizinho', 50, 'soja', current_date - 20, 'V4', -17.8, -50.9);
            INSERT INTO erp.gerentes (fazenda_id, nome, telegram_chat_id, whatsapp_telefone)
                VALUES (1, 'Maria', 111, 5564999998888), (2, 'João', 222, NULL);
            INSERT INTO erp.insumos (id, fazenda_id, produto, tipo, unidade, preco_unitario, estoque)
                VALUES (1, 1, 'Abacus HC', 'fungicida', 'L', 185, 500),
                       (2, 2, 'Brucia', 'herbicida', 'L', 90, 100);
            INSERT INTO erp.maquinas (id, fazenda_id, nome, tipo)
                VALUES (1, 1, 'Pulverizador A', 'pulverizador'), (2, 2, 'Pulverizador Z', 'pulverizador');
            """
        )
        inicio = AGORA + timedelta(days=400)  # sempre no futuro, para "próxima OS"
        for os_id, talhao, insumo, maquina, status in (
            (1, 1, 1, 1, "suspensa_preventiva"),
            (2, 2, 2, 2, "agendada"),
        ):
            await conn.execute(
                "INSERT INTO erp.ordens_servico (id, fazenda_id, talhao_id, tipo_operacao, insumo_id,"
                " dose_por_ha, inicio_previsto, maquina_id, status)"
                " VALUES ($1, $2, $2, 'pulverizacao', $3, 0.3, $4, $5, $6)",
                os_id, talhao, insumo, inicio, maquina, status,
            )  # fmt: skip
        gatilho = json.dumps([{"tipo": "chuva", "valor": 80, "limiar": 20}])
        for inc, talhao in ((1, 1), (2, 2)):
            await conn.execute(
                "INSERT INTO agente.incidentes (id, talhao_id, gatilho, previsao_snapshot, status)"
                " VALUES ($1, $2, $3::jsonb, '[]', 'aguardando_aprovacao')",
                inc, talhao, gatilho,
            )  # fmt: skip
            await conn.execute(
                "INSERT INTO agente.planos (incidente_id, opcoes_json) VALUES ($1, $2::jsonb)",
                inc, json.dumps({"mensagem": f"plano do incidente {inc}"}),
            )  # fmt: skip
            await conn.execute(
                "INSERT INTO agente.impactos"
                " (incidente_id, os_id, talhao_id, condicao, fonte, perda_rs, situacao)"
                " VALUES ($1, $1, $2, 'chuva', 'estimativa', 1665, 'suspensa')",
                inc, talhao,
            )  # fmt: skip
            await conn.execute(
                "INSERT INTO agente.auditoria (incidente_id, no, acao, justificativa)"
                " VALUES ($1, 'acao_preventiva', 'suspender_os', 'bula p. 7')",
                inc,
            )
        horas = [{"ts": (AGORA + timedelta(hours=h)).isoformat(), "chuva_mm": 1.0} for h in range(6)]
        await conn.execute(
            "INSERT INTO clima.previsoes (talhao_id, fonte, horas) VALUES (1, 'open-meteo', $1::jsonb)",
            json.dumps(horas),
        )
    finally:
        await conn.close()
    repo = RepositorioPostgres.de_url(pg_url)
    yield ConsultasPostgres(repo.engine)
    await repo.engine.dispose()


async def test_escopo_vem_do_contato_do_gerente(consultas):
    assert await consultas.escopo_do_contato("telegram", 111) == MINHA
    assert await consultas.escopo_do_contato("whatsapp", 5564999998888) == MINHA
    assert await consultas.escopo_do_contato("telegram", 999) == Escopo()
    assert await consultas.escopo_do_contato("sms", 111) == Escopo()


async def test_cada_consulta_so_ve_a_propria_fazenda(consultas):
    assert [t["nome"] for t in await consultas.talhoes(MINHA)] == ["Talhão 1 - Sede"]
    assert [f["nome"] for f in await consultas.fazendas(MINHA)] == ["Fazenda Teste"]
    desde, ate = AGORA - timedelta(days=1), AGORA + timedelta(days=500)
    [os] = await consultas.ordens(MINHA, desde, ate, None, None)
    assert (os["id"], os["produto"], os["maquina"], os["status"]) == (
        1,
        "Abacus HC",
        "Pulverizador A",
        "suspensa_preventiva",
    )
    assert await consultas.ordens(MINHA, desde, ate, "agendada", None) == []
    assert await consultas.ordem(MINHA, 2) is None  # OS da vizinha
    assert [i["id"] for i in await consultas.incidentes(MINHA, None, 10)] == [1]
    assert await consultas.incidente(MINHA, 2) is None
    assert [p["incidente_id"] for p in await consultas.planos_pendentes(MINHA)] == [1]
    assert [i["produto"] for i in await consultas.insumos(MINHA)] == ["Abacus HC"]
    assert [m["nome"] for m in await consultas.maquinas(MINHA)] == ["Pulverizador A"]
    assert await consultas.previsao(OUTRA, 1) is None


async def test_terminal_ve_as_duas_fazendas(consultas):
    todas = Escopo(todas=True)
    assert len(await consultas.talhoes(todas)) == 2
    assert len(await consultas.incidentes(todas, "aguardando_aprovacao", 10)) == 2


async def test_detalhes_trazem_o_que_o_painel_mostra(consultas):
    os = await consultas.ordem(MINHA, 1)
    assert os["impactos"][0]["perda_rs"] == 1665.0 and os["execucao"] is None
    inc = await consultas.incidente(MINHA, 1)
    assert inc["planos"][0]["mensagem"] == "plano do incidente 1"
    assert inc["impactos"][0]["situacao"] == "suspensa"
    assert inc["eventos"][0]["acao"] == "suspender_os"
    [insumo] = await consultas.insumos(MINHA)
    assert insumo["comprometido"] == 0  # a OS 1 está suspensa: não compromete estoque
    [maquina] = await consultas.maquinas(MINHA)
    assert maquina["proxima_os"] is None
    previsao = await consultas.previsao(MINHA, 1)
    assert previsao["fonte"] == "open-meteo" and len(previsao["horas"]) == 6


async def test_perda_evitada_agrupa_por_situacao(consultas):
    [linha] = await consultas.perda_evitada(MINHA, date(2000, 1, 1), incluir_simulacoes=True)
    assert (linha["situacao"], linha["ordens"], linha["perda_rs"]) == ("suspensa", 1, 1665.0)
