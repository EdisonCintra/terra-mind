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


async def alertar(deps: Dependencias, texto: str) -> None:
    try:
        await deps.canal.enviar_alerta(texto)
    except Exception:  # noqa: BLE001
        logger.exception("falha ao enviar alerta ao gerente")


def ids(lista: list[int]) -> str:
    return ", ".join(str(i) for i in lista)
