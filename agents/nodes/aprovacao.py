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
from agents.nodes.comum import alertar, alvo_das_os, auditar, ids, situar_impactos

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
            await alertar(deps, ctx, f"Não consegui enviar o plano {plano_id}; aprove pelo painel.")
        await auditar(
            deps,
            ctx,
            "aprovacao",
            "pedir_aprovacao",
            depois={"plano_id": plano_id, "custos_atraso": ctx.state.get("custos_atraso") or []},
        )
        yield RequestInput(
            interrupt_id=interrupt_id(plano_id),
            message=ctx.state["mensagem"],
            payload={"incidente_id": incidente_id, "plano_id": plano_id},
        )

    return aprovacao


async def _recusar(deps: Dependencias, ctx: Context, motivo: str, aviso: str) -> Event:
    """Opções de remarcação esgotadas: encerra o incidente, sem decisão manual.

    As OS já suspensas continuam `suspensa_preventiva`; o gerente é avisado e o Monitor volta a poder
    abrir incidente no talhão.
    """
    suspensas = ctx.state.get("os_suspensas") or []
    alvo = alvo_das_os(suspensas)
    ctx.state["motivo_encerramento"] = motivo
    await alertar(deps, ctx, f"{aviso}\n{alvo}. Nenhuma outra ação foi feita.")
    return Event(route="NAO")


def criar_rota_resposta(deps: Dependencias):
    async def rota_resposta(ctx: Context, node_input):
        incidente_id = ctx.state["incidente_id"]
        await deps.repo.mudar_status(incidente_id, "aberto", de={"aguardando_aprovacao"})
        try:
            r = RespostaGerente.model_validate(node_input)
        except ValidationError:
            ctx.state["motivo_manual"] = {"codigo": "resposta_invalida", "detalhe": str(node_input)}
            return Event(route="INVALIDA")  # erro do sistema: segue para a decisão manual
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
            return await _recusar(
                deps,
                ctx,
                f"as opções de remarcação se esgotaram ({feitos} re-planejamentos)",
                "As opções de remarcação se esgotaram sem aprovação.",
            )
        ctx.state["mantida_por"] = r.respondido_por
        return Event(route="MANTER")

    return rota_resposta


def criar_manter_data(deps: Dependencias):
    """ "Manter data original" (resposta NAO): o gerente recusa a nova data e assume o risco do clima.

    Cada OS suspensa volta a `agendada` no horário de antes (`retomar_os` no ERP, com auditoria). A que não
    puder voltar (data original já passou, máquina ocupada no horário) segue suspensa e vai para a decisão
    manual, para o gerente remarcar ou cancelar pelo painel.
    """

    async def manter_data(ctx: Context, node_input):
        plano_id = ctx.state["plano_id"]
        quem = ctx.state.get("mantida_por") or "gerente"
        mantidas: list[int] = []
        falhas: list[str] = []
        for os_id in ctx.state.get("os_suspensas") or []:
            try:
                resultado = await deps.erp.retomar_os(os_id, plano_id)
            except Exception as e:  # noqa: BLE001 - ERP fora do ar: a OS segue suspensa
                resultado = {"ok": False, "erro": f"{type(e).__name__}: {e}"}
            if resultado.get("ok"):
                mantidas.append(os_id)
            else:
                falhas.append(f"OS {os_id}: {resultado.get('erro')}")
        await auditar(
            deps,
            ctx,
            "manter_data",
            "manter_data_original",
            depois={"plano_id": plano_id, "mantidas": mantidas, "falhas": falhas},
            justificativa=f"{quem} manteve a data original, assumindo o risco do clima",
        )
        ctx.state["os_suspensas"] = [o for o in ctx.state.get("os_suspensas") or [] if o not in mantidas]
        if falhas:
            ctx.state["motivo_manual"] = {
                "codigo": "manter_data_falhou",
                "detalhe": "; ".join(falhas),
                "mantidas": mantidas,
            }
            return Event(route="falhou")
        await situar_impactos(deps, ctx, "mantida", de={"suspensa"}, plano_id=plano_id)
        ctx.state["motivo_encerramento"] = f"data original mantida por {quem}, com o risco do clima"
        await alertar(
            deps,
            ctx,
            f"Data original mantida por {quem}: OS {ids(mantidas)} voltou(aram) a AGENDADA(S) no horário de "
            "antes. O risco do clima apontado pela bula continua valendo.",
        )
        return Event(route="ok")

    return manter_data
