"""Aprovação (decisão A2): pausa com `RequestInput(interrupt_id="aprovacao:{plano_id}")`.

A retomada é uma `FunctionResponse(name="adk_request_input", id="aprovacao:{plano_id}")` com
`{"resposta", "respondido_por"}`, que vira a entrada de `rota_resposta` (ver `agents.runner`).
"""

import logging

from google.adk import Context, Event
from google.adk.events import RequestInput
from pydantic import ValidationError

from agents.deps import Dependencias
from agents.modelos import RespostaGerente
from agents.nodes.comum import alertar, auditar

logger = logging.getLogger(__name__)


def interrupt_id(plano_id: int) -> str:
    return f"aprovacao:{plano_id}"


def criar_aprovacao(deps: Dependencias):
    async def aprovacao(ctx: Context, node_input):
        incidente_id = ctx.state["incidente_id"]
        plano_id = ctx.state["plano_id"]
        await deps.repo.mudar_status(
            incidente_id, "aguardando_aprovacao", de={"aberto", "aguardando_aprovacao"}
        )
        try:
            await deps.canal.pedir_aprovacao(incidente_id, plano_id, ctx.state["mensagem"])
        except Exception:  # noqa: BLE001 - o plano continua pendente no painel
            logger.exception("falha ao pedir aprovação pelo canal")
            await alertar(deps, f"Não consegui enviar o plano {plano_id}; aprove pelo painel.")
        await auditar(deps, ctx, "aprovacao", "pedir_aprovacao", depois={"plano_id": plano_id})
        yield RequestInput(
            interrupt_id=interrupt_id(plano_id),
            message=ctx.state["mensagem"],
            payload={"incidente_id": incidente_id, "plano_id": plano_id},
        )

    return aprovacao


def criar_rota_resposta(deps: Dependencias):
    async def rota_resposta(ctx: Context, node_input):
        incidente_id = ctx.state["incidente_id"]
        await deps.repo.mudar_status(incidente_id, "aberto", de={"aguardando_aprovacao"})
        try:
            r = RespostaGerente.model_validate(node_input)
        except ValidationError:
            ctx.state["motivo_manual"] = {"codigo": "resposta_invalida", "detalhe": str(node_input)}
            return Event(route="NAO")
        # Pela API ou pelo Telegram, o Orquestrador já gravou (aqui volta "repetida", sem efeito).
        # No `adk web` / `adk run` a resposta só chega por aqui; o MCP exige o SIM em agente.planos.
        await deps.repo.registrar_resposta(ctx.state["plano_id"], incidente_id, r.resposta, r.respondido_por)
        await auditar(deps, ctx, "aprovacao", "resposta_gerente", depois=r.model_dump())
        if r.resposta == "SIM":
            return Event(route="SIM")
        if r.resposta == "OPCOES":
            feitos = ctx.state.get("replanejamentos", 0)
            if feitos < deps.settings.max_replanejamentos:
                ctx.state["replanejamentos"] = feitos + 1
                ctx.state["pedido_gerente"] = (
                    f"Proponha datas diferentes das já oferecidas: {ctx.state.get('datas_oferecidas')}."
                )
                return Event(route="OPCOES")
            ctx.state["motivo_manual"] = {
                "codigo": "opcoes_esgotadas",
                "detalhe": f"{feitos} re-planejamentos",
            }
            return Event(route="NAO")
        ctx.state["motivo_manual"] = {"codigo": "gerente_recusou", "detalhe": r.respondido_por}
        return Event(route="NAO")

    return rota_resposta
