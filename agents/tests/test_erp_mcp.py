"""`ErpMcp` contra um servidor FastMCP de verdade, com as assinaturas do plano do Felipe (seção 7.3)."""

import asyncio
import socket
from datetime import datetime

import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP

from agents.clientes.erp import ErpIndisponivel, ErpMcp
from agents.tests.fakes import AGORA

OS = {"id": 1, "talhao_id": 1, "status": "agendada", "inicio_previsto": "2026-11-04T04:00:00-03:00"}


def servidor_erp(porta: int) -> FastMCP:
    mcp = FastMCP("erp", host="127.0.0.1", port=porta)
    recebidos: dict = {}

    @mcp.tool()
    def listar_os(inicio: datetime, fim: datetime, talhao_id: int | None = None) -> list[dict]:
        """Lista OS."""
        recebidos["listar_os"] = (inicio, fim, talhao_id)
        return [OS]

    @mcp.tool()
    def consultar_talhao(talhao_id: int) -> dict:
        """Talhão."""
        return {"id": talhao_id, "cultura": "milho", "estagio_fenologico": "V6"}

    @mcp.tool()
    def suspender_os(os_id: int, incidente_id: int, motivo: str) -> dict:
        """Suspende."""
        if os_id != 1:
            return {"ok": False, "erro": f"OS {os_id} não está agendada"}
        return {"ok": True, "os": {**OS, "status": "suspensa_preventiva"}}

    @mcp.tool()
    def reagendar_os(os_id: int, novo_inicio: datetime, plano_id: int) -> dict:
        """Reagenda."""
        raise ValueError("plano não aprovado")

    mcp.recebidos = recebidos  # type: ignore[attr-defined]
    return mcp


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def erp_real():
    porta = porta_livre()
    mcp = servidor_erp(porta)
    servidor = uvicorn.Server(
        uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=porta, log_level="error")
    )
    tarefa = asyncio.create_task(servidor.serve())
    while not servidor.started:
        await asyncio.sleep(0.05)
    yield ErpMcp(f"http://127.0.0.1:{porta}/mcp"), mcp
    servidor.should_exit = True
    await tarefa


async def test_leitura_lista_e_item(erp_real):
    erp, mcp = erp_real
    assert await erp.listar_os(AGORA, AGORA, 1) == [OS]
    inicio, _, talhao_id = mcp.recebidos["listar_os"]
    assert inicio == AGORA and talhao_id == 1  # datetime com fuso atravessa o MCP
    assert (await erp.consultar_talhao(1))["estagio_fenologico"] == "V6"


async def test_escrita_ok_e_recusada(erp_real):
    erp, _ = erp_real
    assert (await erp.suspender_os(1, 7, "chuva"))["ok"] is True
    assert await erp.suspender_os(2, 7, "chuva") == {"ok": False, "erro": "OS 2 não está agendada"}


async def test_excecao_no_servidor_vira_ok_false(erp_real):
    erp, _ = erp_real
    r = await erp.reagendar_os(1, AGORA, 1)
    assert r["ok"] is False and "plano não aprovado" in r["erro"]


async def test_servidor_fora_do_ar():
    erp = ErpMcp(f"http://127.0.0.1:{porta_livre()}/mcp", timeout_s=2)
    with pytest.raises(ErpIndisponivel, match="listar_os: Connect"):
        await erp.listar_os(AGORA, AGORA)
    assert (await erp.suspender_os(1, 7, "x"))["ok"] is False
