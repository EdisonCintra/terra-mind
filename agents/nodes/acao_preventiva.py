"""Nó Ação Preventiva: a única escrita sem aprovação (`agendada → suspensa_preventiva`)."""

import logging

from google.adk import Context, Event

from agents.agentes._comum import ENTRADA_LLM
from agents.deps import Dependencias
from agents.modelos import AvaliacaoRisco, OsEmRisco, como_json
from agents.nodes.comum import auditar
from agents.rules.impacto import calcular_impacto, linha_de_impacto

logger = logging.getLogger(__name__)


def criar_acao_preventiva(deps: Dependencias):
    async def acao_preventiva(ctx: Context, node_input):
        incidente_id = ctx.state["incidente_id"]
        os_por_id = {o["os_id"]: OsEmRisco.model_validate(o) for o in ctx.state["os_em_risco"]}
        avaliacao = AvaliacaoRisco.model_validate(ctx.state["avaliacao"])
        suspensas: list[int] = []
        impactos: list[dict] = []
        registros: list[dict] = []  # G1: o que vai para agente.impactos (contrato 25)
        falhas: list[str] = []
        # Uma OS pode violar mais de um limite: suspende uma vez, com todas as justificativas.
        for os_id, violacoes in avaliacao.violacoes_por_os().items():
            motivo = "; ".join(f"{v.justificativa} (fonte: {v.fonte_doc}, p. {v.pagina})" for v in violacoes)
            r = await deps.erp.suspender_os(os_id, incidente_id, motivo)
            if not r.get("ok"):
                falhas.append(f"OS {os_id}: {r.get('erro')}")
                continue
            suspensas.append(os_id)
            # Perdas por condições diferentes não se somam: vale a maior.
            calculados = [
                (p, await calcular_impacto(os_por_id[os_id], p, deps.conhecimento))
                for p in dict.fromkeys(v.parametro for v in violacoes)
            ]
            condicao, maior = max(calculados, key=lambda par: par[1].perda_rs or 0)
            impactos.append(como_json(maior))
            registros.append(linha_de_impacto(os_por_id[os_id], condicao, maior))
        ctx.state["os_suspensas"] = suspensas
        ctx.state["impactos"] = impactos
        await auditar(
            deps,
            ctx,
            "acao_preventiva",
            "suspender_os",
            depois={"suspensas": suspensas, "impactos": impactos, "falhas": falhas},
        )
        if registros:
            try:
                await deps.repo.gravar_impactos(incidente_id, registros)
            except Exception:  # noqa: BLE001 - o dashboard não pode impedir a suspensão
                logger.exception("falha ao gravar os impactos do incidente %s", incidente_id)
        if not suspensas:
            ctx.state["motivo_manual"] = {"codigo": "nenhuma_os_suspensa", "detalhe": "; ".join(falhas)}
            return Event(route="nenhuma")
        return Event(route="suspensas", output=ENTRADA_LLM)

    return acao_preventiva
