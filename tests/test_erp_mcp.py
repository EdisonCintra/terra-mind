"""Testes das ferramentas do MCP do ERP contra o Postgres real (T7.8).

Precisam do banco no ar (ver `tests/conftest.py`); cada teste recomeça do seed.
"""

import asyncio
import os
import socket
from datetime import datetime, timedelta

import psycopg
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from erp_mcp import escrita, leitura
from erp_mcp.server import FERRAMENTAS, mcp

DATABASE_URL = os.environ["DATABASE_URL"]


def consultar(sql: str, *args):
    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        return conn.execute(sql, args).fetchall()


def criar_plano(aprovado: bool) -> int:
    """Cria incidente + plano; `aprovado=True` simula o SIM do gerente."""
    with psycopg.connect(DATABASE_URL) as conn:
        inc = conn.execute(
            "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot)"
            " VALUES (1, '[]', '[]') RETURNING id"
        ).fetchone()[0]
        return conn.execute(
            "INSERT INTO agente.planos (incidente_id, opcoes_json, aprovado_por, respondido_em)"
            " VALUES (%s, '{}', %s, %s) RETURNING id",
            (inc, "gerente" if aprovado else None, datetime.now().astimezone() if aprovado else None),
        ).fetchone()[0]


def agora() -> datetime:
    return datetime.now().astimezone()


# ---------- leitura ----------


def test_listar_os_devolve_as_chaves_do_contrato_16():
    [os_cenario] = leitura.listar_os(agora(), agora() + timedelta(hours=24))
    esperadas = {
        "id", "talhao_id", "talhao", "area_ha", "status", "tipo_operacao", "insumo_id", "produto",
        "tipo_insumo", "dose_por_ha", "unidade", "preco_unitario", "inicio_previsto",
        "duracao_horas", "maquina_id", "maquina_tipo", "operador_id",
    }  # fmt: skip
    assert esperadas <= set(os_cenario)
    assert os_cenario["produto"] == "Abacus HC" and os_cenario["tipo_insumo"] == "fungicida"
    assert os_cenario["status"] == "agendada" and os_cenario["maquina_tipo"] == "pulverizador"


def test_listar_os_filtra_janela_e_talhao():
    todas = leitura.listar_os(agora(), agora() + timedelta(days=30))
    assert [o["id"] for o in todas] == [1, 2, 3]
    assert [o["id"] for o in leitura.listar_os(agora(), agora() + timedelta(days=30), talhao_id=2)] == [2]


def test_detalhar_consultar_e_inexistentes():
    assert leitura.detalhar_os(1)["id"] == 1
    assert leitura.detalhar_os(99)["ok"] is False
    assert leitura.consultar_talhao(1)["estagio_fenologico"] == "V6"
    assert leitura.consultar_talhao(99)["ok"] is False


def test_disponibilidade_considera_sobreposicao_e_tipo():
    inicio = agora() + timedelta(hours=20)
    ocupadas = leitura.disponibilidade_maquinas(inicio, inicio + timedelta(hours=2), "pulverizador")
    assert [m["nome"] for m in ocupadas] == ["Pulverizador B"]  # A está na OS 1
    depois = leitura.disponibilidade_maquinas(inicio + timedelta(hours=9), inicio + timedelta(hours=12))
    assert {m["nome"] for m in depois} == {"Pulverizador A", "Pulverizador B"}
    assert leitura.disponibilidade_maquinas(inicio, inicio + timedelta(hours=2), "colheitadeira") == []


def test_operadores_e_leituras_solo():
    assert [o["nome"] for o in leitura.listar_operadores()] == ["João", "Carlos"]
    leituras = leitura.leituras_solo(1, horas=24)
    assert 24 <= len(leituras) <= 25 and leituras[0]["ts"] > leituras[-1]["ts"]


# ---------- escrita ----------


def test_suspender_os_funciona_e_audita():
    plano = criar_plano(aprovado=False)
    inc = consultar("SELECT incidente_id FROM agente.planos WHERE id = %s", plano)[0]["incidente_id"]
    r = escrita.suspender_os(1, inc, "Abacus HC, p. 7: 4 h sem chuva")
    assert r["ok"] is True and r["os"]["status"] == "suspensa_preventiva"
    [aud] = consultar("SELECT * FROM agente.auditoria")
    assert aud["acao"] == "suspender_os" and aud["no"] == "acao_preventiva"
    assert aud["antes"]["status"] == "agendada" and aud["depois"]["status"] == "suspensa_preventiva"
    assert aud["justificativa"].startswith("Abacus HC")


def test_suspender_de_novo_falha():
    inc = criar_incidente()
    assert escrita.suspender_os(1, inc, "x")["ok"] is True
    r = escrita.suspender_os(1, inc, "x")
    assert r["ok"] is False and "não está agendada" in r["erro"]
    assert len(consultar("SELECT 1 FROM agente.auditoria")) == 1  # a falha não audita


def test_suspender_com_incidente_inexistente_falha_sem_alterar():
    r = escrita.suspender_os(1, 999, "x")
    assert r["ok"] is False and "incidente 999" in r["erro"]
    assert consultar("SELECT status FROM erp.ordens_servico WHERE id = 1")[0]["status"] == "agendada"


def test_reagendar_sem_plano_aprovado_falha_e_nao_altera():
    escrita.suspender_os(1, criar_incidente(), "x")
    nao_aprovado = criar_plano(aprovado=False)
    novo = agora() + timedelta(days=3)
    assert escrita.reagendar_os(1, novo, nao_aprovado) == {"ok": False, "erro": "plano não aprovado"}
    assert escrita.reagendar_os(1, novo, 999)["ok"] is False
    assert (
        consultar("SELECT status FROM erp.ordens_servico WHERE id = 1")[0]["status"] == "suspensa_preventiva"
    )


def test_reagendar_repetido_falha():
    escrita.suspender_os(1, criar_incidente(), "x")
    plano = criar_plano(aprovado=True)
    novo = agora() + timedelta(days=3)
    r = escrita.reagendar_os(1, novo, plano)
    assert r["ok"] is True and r["os"]["status"] == "reagendada"
    repetido = escrita.reagendar_os(1, novo + timedelta(days=1), plano)
    assert repetido["ok"] is False and "não está suspensa" in repetido["erro"]


def test_reagendar_nao_mexe_em_os_agendada():
    plano = criar_plano(aprovado=True)
    r = escrita.reagendar_os(1, agora() + timedelta(days=3), plano)
    assert r["ok"] is False and "agendada" in r["erro"]


def test_alocar_maquina_livre_ocupada_e_repetida():
    escrita.suspender_os(1, criar_incidente(), "x")
    plano = criar_plano(aprovado=True)
    assert escrita.alocar_maquina(1, 2, 999) == {"ok": False, "erro": "plano não aprovado"}
    # Pulverizador B está na OS 3 (daqui a 8 dias): mover a OS 1 para lá cria sobreposição.
    escrita.reagendar_os(1, agora() + timedelta(days=8, hours=1), plano)
    ocupada = escrita.alocar_maquina(1, 2, plano)
    assert ocupada["ok"] is False and "não está livre" in ocupada["erro"]
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("UPDATE erp.ordens_servico SET inicio_previsto = now() + interval '3 days' WHERE id = 1")
    livre = escrita.alocar_maquina(1, 2, plano)
    assert livre["ok"] is True and livre["os"]["maquina_id"] == 2
    assert escrita.alocar_maquina(1, 2, plano)["ok"] is False  # já alocada


def test_alocar_maquina_em_manutencao_falha():
    escrita.suspender_os(1, criar_incidente(), "x")
    plano = criar_plano(aprovado=True)
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute("UPDATE erp.maquinas SET status = 'manutencao' WHERE id = 2")
    r = escrita.alocar_maquina(1, 2, plano)
    assert r["ok"] is False and "não está livre" in r["erro"]


def test_reservar_estoque_ok_acima_do_estoque_e_repetido():
    plano = criar_plano(aprovado=True)
    assert escrita.reservar_estoque(1, 36, 999) == {"ok": False, "erro": "plano não aprovado"}
    assert escrita.reservar_estoque(1, 0, plano)["ok"] is False
    acima = escrita.reservar_estoque(1, 501, plano)
    assert acima["ok"] is False and "insuficiente" in acima["erro"]
    assert escrita.reservar_estoque(1, 36, plano)["ok"] is True
    assert float(consultar("SELECT estoque FROM erp.insumos WHERE id = 1")[0]["estoque"]) == 464.0
    repetida = escrita.reservar_estoque(1, 36, plano)
    assert repetida["ok"] is False and "já reservado" in repetida["erro"]
    assert float(consultar("SELECT estoque FROM erp.insumos WHERE id = 1")[0]["estoque"]) == 464.0


def test_sem_plano_aprovado_so_passa_a_suspensao():
    """Regra 3: nenhuma ferramenta que compromete recursos aceita plano pendente."""
    pendente = criar_plano(aprovado=False)
    novo = agora() + timedelta(days=3)
    assert escrita.reagendar_os(1, novo, pendente)["ok"] is False
    assert escrita.alocar_maquina(1, 2, pendente)["ok"] is False
    assert escrita.reservar_estoque(1, 1, pendente)["ok"] is False
    assert escrita.suspender_os(1, criar_incidente(), "x")["ok"] is True


def criar_incidente() -> int:
    with psycopg.connect(DATABASE_URL) as conn:
        return conn.execute(
            "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)"
            " VALUES (2, '[]', '[]', 'aberto') RETURNING id"
        ).fetchone()[0]


# ---------- servidor MCP pelo protocolo ----------


def test_servidor_nao_expoe_janelas_validas():
    nomes = {f.__name__ for f in FERRAMENTAS}
    assert "janelas_validas" not in nomes and len(nomes) == 10


async def test_servidor_responde_pelo_streamable_http():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    servidor = uvicorn.Server(
        uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=porta, log_level="error")
    )
    tarefa = asyncio.create_task(servidor.serve())
    while not servidor.started:
        await asyncio.sleep(0.05)
    try:
        async with streamable_http_client(f"http://127.0.0.1:{porta}/mcp") as (le, escreve, _):
            async with ClientSession(le, escreve) as sessao:
                await sessao.initialize()
                ferramentas = {t.name for t in (await sessao.list_tools()).tools}
                assert {"listar_os", "suspender_os", "reagendar_os"} <= ferramentas
                inicio = agora().isoformat()
                fim = (agora() + timedelta(hours=24)).isoformat()
                lista = await sessao.call_tool("listar_os", {"inicio": inicio, "fim": fim})
                assert lista.isError is False
                assert lista.structuredContent["result"][0]["produto"] == "Abacus HC"
                inc = criar_incidente()
                suspensa = await sessao.call_tool(
                    "suspender_os", {"os_id": 1, "incidente_id": inc, "motivo": "teste"}
                )
                assert suspensa.structuredContent["ok"] is True
                de_novo = await sessao.call_tool(
                    "suspender_os", {"os_id": 1, "incidente_id": inc, "motivo": "teste"}
                )
                assert de_novo.structuredContent["ok"] is False
    finally:
        servidor.should_exit = True
        await tarefa
