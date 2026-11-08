"""Escalonamento de aprovações sem resposta: alerta uma vez e a sessão segue pausada.

G5: o lembrete traz, por OS, quando a janela proposta vence e quanto custa perdê-la. Os números vêm
de `opcoes_json["custos_atraso"]`, calculados em código quando o plano foi proposto.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from pydantic import ValidationError

from agents.canal import Canal
from agents.clientes.repositorio import RepositorioAgente
from agents.modelos import CustoAtraso
from agents.rules.atraso import texto_lembrete

logger = logging.getLogger(__name__)


def custos_do_plano(opcoes_json: dict | None) -> list[CustoAtraso]:
    try:
        return [CustoAtraso.model_validate(c) for c in (opcoes_json or {}).get("custos_atraso") or []]
    except ValidationError:
        logger.warning("custos de atraso ilegíveis no plano; lembrete sem eles")
        return []


async def verificar_timeouts(
    repo: RepositorioAgente, canal: Canal, agora: datetime, timeout_min: int
) -> list[int]:
    escalados = []
    for pendente in await repo.planos_para_escalar(agora, timeout_min):
        plano_id, incidente_id = pendente["plano_id"], pendente["incidente_id"]
        custos = custos_do_plano(pendente.get("opcoes_json"))
        texto = texto_lembrete(incidente_id, plano_id, pendente["criado_em"], agora, custos)
        try:
            await canal.enviar_alerta(texto)
        except Exception:  # noqa: BLE001
            logger.exception("falha ao escalonar o plano %s", plano_id)
        await repo.marcar_escalado(plano_id, agora)
        await repo.auditar(
            incidente_id, "timeout", "escalonar_aprovacao", depois={"plano_id": plano_id}, justificativa=texto
        )
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
