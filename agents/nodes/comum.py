import logging
from typing import Any

from google.adk import Context

from agents.deps import Dependencias

logger = logging.getLogger(__name__)


async def auditar(deps: Dependencias, ctx: Context, no: str, acao: str, **dados: Any) -> None:
    """Auditoria nunca derruba o fluxo."""
    try:
        await deps.repo.auditar(ctx.state.get("incidente_id"), no, acao, **dados)
    except Exception:  # noqa: BLE001
        logger.exception("falha ao gravar auditoria %s/%s", no, acao)


async def alertar(deps: Dependencias, ctx: Context, texto: str) -> None:
    """Alerta ao gerente. Vai com o `incidente_id`: é por ele que o canal acha o gerente da fazenda."""
    try:
        await deps.canal.enviar_alerta(texto, incidente_id=ctx.state.get("incidente_id"))
    except Exception:  # noqa: BLE001
        logger.exception("falha ao enviar alerta ao gerente")


async def situar_impactos(deps: Dependencias, ctx: Context, situacao: str, **filtros: Any) -> None:
    """G1: atualiza a situação da perda em `agente.impactos`. Como a auditoria, nunca derruba o fluxo."""
    incidente_id = ctx.state.get("incidente_id")
    if incidente_id is None:
        return
    try:
        await deps.repo.situar_impactos(incidente_id, situacao, **filtros)
    except Exception:  # noqa: BLE001
        logger.exception("falha ao atualizar os impactos do incidente %s", incidente_id)


def ids(lista: list[int]) -> str:
    return ", ".join(str(i) for i in lista)


def alvo_das_os(suspensas: list[int]) -> str:
    return f"OS {ids(suspensas)} segue(m) SUSPENSA(S)" if suspensas else "Nenhuma OS foi alterada"
