"""Roteamento depois do Avaliador de Risco (decisão A4), que roda como nó `Agent` do grafo.

Falha do LLM ou saída fora do schema (marcadas pelas proteções do agente) → rota `falhou`: a OS
não é suspensa (decisão A5).
"""

from google.adk import Context, Event

from agents.agentes._comum import chave_falha
from agents.deps import Dependencias
from agents.modelos import AvaliacaoRisco, como_json
from agents.nodes.comum import auditar


def criar_rota_avaliacao(deps: Dependencias):
    async def rota_avaliacao(ctx: Context, node_input):
        falha = ctx.state.get(chave_falha("avaliador_risco"))
        if falha:
            ctx.state[chave_falha("avaliador_risco")] = None
            ctx.state["motivo_manual"] = {"codigo": "avaliacao_indisponivel", "detalhe": falha}
            await auditar(deps, ctx, "avaliador", "avaliacao_falhou", justificativa=falha)
            return Event(route="falhou")
        avaliacao = AvaliacaoRisco.model_validate(node_input)
        # O LLM só decide sobre as OS que recebeu; ids desconhecidos são descartados.
        conhecidas = {o["os_id"] for o in ctx.state["os_em_risco"]}
        avaliacao.violacoes = [v for v in avaliacao.violacoes if v.os_id in conhecidas]
        ctx.state["avaliacao"] = como_json(avaliacao)
        await auditar(deps, ctx, "avaliador", "avaliar_risco", depois=como_json(avaliacao))
        if not any(v.viola for v in avaliacao.violacoes):
            ctx.state["motivo_encerramento"] = "Avaliador não encontrou violação citável"
            return Event(route="sem_violacao")
        return Event(route="violacao")

    return rota_avaliacao
