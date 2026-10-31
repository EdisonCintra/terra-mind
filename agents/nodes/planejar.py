"""Planejamento: o Supervisor (nó `Agent` do grafo) propõe e o Validador (código) confere.

    preparar_plano → supervisor → validador ─┬─ valido  → aprovacao
                         ↑______ revisar ____┤
                                             └─ reprovado → decisao_manual

`preparar_plano` zera o contador de voltas a cada rodada: depois das análises, de "Ver opções" e do
Executor quando a janela aprovada deixou de valer. O estado traz `pedido_gerente` ou
`motivo_reprovacao` para a próxima proposta. O laço é limitado por `max_voltas_validador`.
"""

from google.adk import Context, Event

from agents.agentes._comum import ENTRADA_LLM, chave_falha
from agents.deps import Dependencias
from agents.modelos import Impacto, PlanoProposto, como_json
from agents.nodes.comum import auditar
from agents.rules.mensagem import compor_mensagem
from agents.rules.validador import ContextoPlano, validar


def criar_preparar_plano(deps: Dependencias):
    async def preparar_plano(ctx: Context, node_input):
        ctx.state["voltas_validador"] = 0
        return ENTRADA_LLM

    return preparar_plano


def criar_validador(deps: Dependencias):
    async def validador(ctx: Context, node_input):
        volta = ctx.state.get("voltas_validador", 0) + 1
        ctx.state["voltas_validador"] = volta
        falha = ctx.state.get(chave_falha("supervisor"))
        contexto = ContextoPlano.do_estado(ctx.state)
        aprovado: PlanoProposto | None = None
        if falha:
            ctx.state[chave_falha("supervisor")] = None
            motivos = [f"Supervisor sem plano válido ({falha})"]
        else:
            plano = PlanoProposto.model_validate(node_input)
            veredito = await validar(plano, contexto, deps.erp, deps.relogio())
            await auditar(
                deps,
                ctx,
                "validador",
                "validar_plano",
                depois={"volta": volta, "plano": como_json(plano), "veredito": como_json(veredito)},
            )
            motivos = veredito.motivos
            if veredito.aprovado:
                aprovado = plano

        if aprovado is None:
            if volta < deps.settings.max_voltas_validador:
                ctx.state["motivo_reprovacao"] = "; ".join(motivos)
                return Event(route="revisar", output=ENTRADA_LLM)
            ctx.state["motivo_reprovacao"] = None
            ctx.state["pedido_gerente"] = None
            ctx.state["motivo_manual"] = {"codigo": "plano_reprovado", "detalhe": "; ".join(motivos)}
            return Event(route="reprovado")

        ctx.state["motivo_reprovacao"] = None
        ctx.state["pedido_gerente"] = None
        impactos = {i["os_id"]: Impacto.model_validate(i) for i in ctx.state.get("impactos") or []}
        mensagem = compor_mensagem(aprovado, contexto.os_por_id, contexto.violacoes_por_os, impactos)
        plano_id = await deps.repo.criar_plano(
            ctx.state["incidente_id"], {"opcoes": como_json(aprovado)["opcoes"], "mensagem": mensagem}
        )
        oferecidas = dict(ctx.state.get("datas_oferecidas") or {})
        for opcao in aprovado.opcoes:
            oferecidas[str(opcao.os_id)] = [
                *oferecidas.get(str(opcao.os_id), []),
                opcao.novo_inicio.isoformat(),
            ]
        ctx.state["datas_oferecidas"] = oferecidas
        ctx.state["plano"] = como_json(aprovado)
        ctx.state["plano_id"] = plano_id
        ctx.state["mensagem"] = mensagem
        await auditar(
            deps, ctx, "supervisor", "propor_plano", depois={"plano_id": plano_id, **como_json(aprovado)}
        )
        return Event(route="valido")

    return validador
