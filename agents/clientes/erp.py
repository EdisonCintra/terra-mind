"""Acesso ao mini-ERP pelo servidor MCP do Felipe (contrato 5).

Leituras devolvem dados Python e levantam `ErpIndisponivel` se o servidor falhar.
Escritas devolvem sempre `{"ok": bool, ...}` e nunca levantam.
"""

import json
import logging
from datetime import datetime
from typing import Any, Protocol

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

logger = logging.getLogger(__name__)


class ErpIndisponivel(Exception):
    pass


class ErpCliente(Protocol):
    async def listar_os(
        self, inicio: datetime, fim: datetime, talhao_id: int | None = None
    ) -> list[dict]: ...
    async def detalhar_os(self, os_id: int) -> dict: ...
    async def consultar_talhao(self, talhao_id: int) -> dict: ...
    async def disponibilidade_maquinas(
        self, inicio: datetime, fim: datetime, tipo: str | None = None
    ) -> list[dict]: ...
    async def listar_operadores(self) -> list[dict]: ...
    async def suspender_os(self, os_id: int, incidente_id: int, motivo: str) -> dict: ...
    async def reagendar_os(self, os_id: int, novo_inicio: datetime, plano_id: int) -> dict: ...
    async def alocar_maquina(self, os_id: int, maquina_id: int, plano_id: int) -> dict: ...
    async def reservar_estoque(self, insumo_id: int, quantidade: float, plano_id: int) -> dict: ...


def _causa(e: BaseException) -> str:
    """O cliente MCP embrulha a falha de rede em `ExceptionGroup`; mostra a exceção de dentro."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return f"{type(e).__name__}: {e}"


def _serializar(args: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in args.items() if v is not None}


def interpretar_resultado(resultado: Any) -> Any:
    """Converte um `CallToolResult` do MCP no valor Python que a ferramenta devolveu.

    O FastMCP põe o retorno em `structuredContent`; listas e escalares vêm embrulhados
    em `{"result": ...}`. Sem `structuredContent`, o JSON está no primeiro bloco de texto.
    """
    texto = next((c.text for c in resultado.content if getattr(c, "type", None) == "text"), None)
    if resultado.isError:
        return {"ok": False, "erro": texto or "erro no servidor MCP"}
    dado = resultado.structuredContent
    if dado is None:
        if texto is None:
            return None
        try:
            dado = json.loads(texto)
        except json.JSONDecodeError:
            return texto
    if isinstance(dado, dict) and set(dado) == {"result"}:
        return dado["result"]
    return dado


class ErpMcp:
    """Cliente MCP por streamable HTTP. Abre uma sessão por chamada: simples e suficiente para a demo."""

    def __init__(self, url: str, timeout_s: float = 15):
        self.url = url
        self.timeout_s = timeout_s

    async def _chamar(self, ferramenta: str, **args: Any) -> Any:
        try:
            async with (
                create_mcp_http_client(timeout=httpx.Timeout(self.timeout_s)) as http,
                streamable_http_client(self.url, http_client=http) as (leitura, escrita, _),
                ClientSession(leitura, escrita) as sessao,
            ):
                await sessao.initialize()
                resultado = await sessao.call_tool(ferramenta, _serializar(args))
        except Exception as e:  # noqa: BLE001 - qualquer falha de rede vira indisponibilidade
            raise ErpIndisponivel(f"{ferramenta}: {_causa(e)}") from e
        return interpretar_resultado(resultado)

    async def _ler_lista(self, ferramenta: str, **args: Any) -> list[dict]:
        dado = await self._chamar(ferramenta, **args)
        if isinstance(dado, dict) and dado.get("ok") is False:
            raise ErpIndisponivel(f"{ferramenta}: {dado.get('erro')}")
        if isinstance(dado, dict):
            # Aceita {"ok": true, "<qualquer chave>": [...]} além da lista pura.
            dado = next((v for v in dado.values() if isinstance(v, list)), [])
        return list(dado or [])

    async def _ler_item(self, ferramenta: str, **args: Any) -> dict:
        dado = await self._chamar(ferramenta, **args)
        if not isinstance(dado, dict) or dado.get("ok") is False:
            raise ErpIndisponivel(f"{ferramenta}: {dado}")
        return next((v for v in dado.values() if isinstance(v, dict)), dado) if "ok" in dado else dado

    async def _escrever(self, ferramenta: str, **args: Any) -> dict:
        try:
            dado = await self._chamar(ferramenta, **args)
        except ErpIndisponivel as e:
            return {"ok": False, "erro": str(e)}
        if not isinstance(dado, dict) or "ok" not in dado:
            return {"ok": False, "erro": f"resposta inesperada de {ferramenta}: {dado}"}
        return dado

    async def listar_os(self, inicio, fim, talhao_id=None):
        return await self._ler_lista("listar_os", inicio=inicio, fim=fim, talhao_id=talhao_id)

    async def detalhar_os(self, os_id):
        return await self._ler_item("detalhar_os", os_id=os_id)

    async def consultar_talhao(self, talhao_id):
        return await self._ler_item("consultar_talhao", talhao_id=talhao_id)

    async def disponibilidade_maquinas(self, inicio, fim, tipo=None):
        return await self._ler_lista("disponibilidade_maquinas", inicio=inicio, fim=fim, tipo=tipo)

    async def listar_operadores(self):
        return await self._ler_lista("listar_operadores")

    async def suspender_os(self, os_id, incidente_id, motivo):
        return await self._escrever("suspender_os", os_id=os_id, incidente_id=incidente_id, motivo=motivo)

    async def reagendar_os(self, os_id, novo_inicio, plano_id):
        return await self._escrever("reagendar_os", os_id=os_id, novo_inicio=novo_inicio, plano_id=plano_id)

    async def alocar_maquina(self, os_id, maquina_id, plano_id):
        return await self._escrever("alocar_maquina", os_id=os_id, maquina_id=maquina_id, plano_id=plano_id)

    async def reservar_estoque(self, insumo_id, quantidade, plano_id):
        return await self._escrever(
            "reservar_estoque", insumo_id=insumo_id, quantidade=quantidade, plano_id=plano_id
        )
