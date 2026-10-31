"""Executor: depois do SIM, revalida a janela e escreve no ERP (cada escrita exige o plano aprovado)."""

import logging

from google.adk import Context, Event

from agents.clientes.erp import ErpIndisponivel
from agents.deps import Dependencias
from agents.modelos import PlanoProposto
from agents.nodes.comum import alertar, auditar, ids
from agents.rules.janelas import formatar_data
from agents.rules.validador import ContextoPlano, validar

logger = logging.getLogger(__name__)

MAX_REVALIDACOES = 1


async def _chat_ids(deps: Dependencias) -> dict[int, int]:
    try:
        operadores = await deps.erp.listar_operadores()
    except ErpIndisponivel:
        return {}
    return {o["id"]: o["telegram_chat_id"] for o in operadores if o.get("telegram_chat_id")}


def criar_executor(deps: Dependencias):
    async def executor(ctx: Context, node_input):
        plano_id = ctx.state["plano_id"]
        plano = PlanoProposto.model_validate(ctx.state["plano"])
        contexto = ContextoPlano.do_estado(ctx.state)
        contexto.datas_oferecidas = {}  # as datas aprovadas estão entre as oferecidas
        # ERP fora do ar não invalida a janela aprovada: as escritas abaixo conferem máquina e plano.
        veredito = await validar(plano, contexto, deps.erp, deps.relogio(), exigir_erp=False)
        if not veredito.aprovado:
            revalidacoes = ctx.state.get("revalidacoes", 0)
            await auditar(deps, ctx, "executor", "revalidacao_falhou", depois=veredito.model_dump())
            if revalidacoes < MAX_REVALIDACOES:
                ctx.state["revalidacoes"] = revalidacoes + 1
                ctx.state["motivo_reprovacao"] = "a janela aprovada deixou de valer: " + "; ".join(
                    veredito.motivos
                )
                await alertar(
                    deps, f"A janela aprovada no plano {plano_id} não vale mais; preparando nova opção."
                )
                return Event(route="revalidar")
            ctx.state["motivo_manual"] = {"codigo": "janela_invalida", "detalhe": "; ".join(veredito.motivos)}
            return Event(route="falhou")

        chat_ids = await _chat_ids(deps)
        feitas: list[int] = []
        executados: list[str] = []  # cada escrita que deu certo, para o gerente saber o que já mudou
        reservou_estoque = False
        for opcao in plano.opcoes:
            os = contexto.os_por_id[opcao.os_id]
            passos = [("reagendar_os", deps.erp.reagendar_os, (opcao.os_id, opcao.novo_inicio, plano_id))]
            if opcao.maquina_id != os.maquina_id:
                passos.append(
                    ("alocar_maquina", deps.erp.alocar_maquina, (opcao.os_id, opcao.maquina_id, plano_id))
                )
            if os.insumo_id is not None and os.dose_por_ha is not None:
                quantidade = round(os.area_ha * os.dose_por_ha, 3)
                passos.append(
                    ("reservar_estoque", deps.erp.reservar_estoque, (os.insumo_id, quantidade, plano_id))
                )
            for nome, ferramenta, args in passos:
                r = await ferramenta(*args)
                if not r.get("ok"):
                    detalhe = f"OS {opcao.os_id}: {nome} falhou ({r.get('erro')})"
                    ctx.state["motivo_manual"] = {
                        "codigo": "falha_execucao",
                        "detalhe": detalhe,
                        "executados": executados,
                    }
                    await auditar(
                        deps,
                        ctx,
                        "executor",
                        "execucao_falhou",
                        depois={"executados": executados},
                        justificativa=detalhe,
                    )
                    return Event(route="falhou")
                executados.append(f"OS {opcao.os_id}: {nome}")
                reservou_estoque = reservou_estoque or nome == "reservar_estoque"
            feitas.append(opcao.os_id)
            chat_id = chat_ids.get(os.operador_id) if os.operador_id else None
            if chat_id:
                texto = (
                    f"OS {os.os_id} ({os.tipo_operacao}, {os.talhao}) remarcada para "
                    f"{formatar_data(opcao.novo_inicio)}, máquina {opcao.maquina_id}."
                )
                try:
                    await deps.canal.notificar_operador(chat_id, texto)
                except Exception:  # noqa: BLE001
                    logger.exception("falha ao notificar operador %s", chat_id)
        await auditar(deps, ctx, "executor", "executar_plano", depois={"plano_id": plano_id, "os": feitas})
        estoque = " e estoque reservado" if reservou_estoque else ""
        await alertar(deps, f"Plano {plano_id} executado: OS {ids(feitas)} remarcadas{estoque}.")
        ctx.state["motivo_encerramento"] = f"plano {plano_id} executado"
        return Event(route="ok")

    return executor
