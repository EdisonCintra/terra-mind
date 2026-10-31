"""Escalonamento de aprovações sem resposta: alerta uma vez e a sessão segue pausada."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from agents.canal import Canal
from agents.clientes.repositorio import RepositorioAgente

logger = logging.getLogger(__name__)


async def verificar_timeouts(
    repo: RepositorioAgente, canal: Canal, agora: datetime, timeout_min: int
) -> list[int]:
    escalados = []
    for pendente in await repo.planos_para_escalar(agora, timeout_min):
        plano_id, incidente_id = pendente["plano_id"], pendente["incidente_id"]
        try:
            await canal.enviar_alerta(
                f"Incidente {incidente_id}: sem resposta ao plano {plano_id} há mais de {timeout_min} min. "
                "As OS seguem suspensas; o plano ainda pode ser aprovado."
            )
        except Exception:  # noqa: BLE001
            logger.exception("falha ao escalonar o plano %s", plano_id)
        await repo.marcar_escalado(plano_id, agora)
        await repo.auditar(incidente_id, "timeout", "escalonar_aprovacao", depois={"plano_id": plano_id})
        escalados.append(plano_id)
    return escalados


async def laco_timeouts(
    repo: RepositorioAgente,
    canal: Callable[[], Canal],
    relogio: Callable[[], datetime],
    timeout_min: int,
    intervalo_s: float,
) -> None:
    while True:
        try:
            await verificar_timeouts(repo, canal(), relogio(), timeout_min)
        except Exception:  # noqa: BLE001 - o laço não pode morrer
            logger.exception("falha ao verificar timeouts")
        await asyncio.sleep(intervalo_s)
