"""Fins do grafo: `encerrar` e `decisao_manual` (contrato 4)."""

from google.adk import Context
from google.genai import types

from agents.deps import Dependencias
from agents.modelos import OsEmRisco
from agents.nodes.comum import alertar, auditar, ids
from agents.rules.impacto import calcular_impacto, formatar_reais

ABERTOS = {"aberto", "aguardando_aprovacao", "aguardando_decisao_manual"}
TEMPO_PREVISTO = {
    "chuva": "a chuva prevista",
    "vento": "o vento previsto",
    "umidade": "a umidade prevista",
    "temperatura": "a temperatura prevista",
}


def texto_decisao_manual(estado) -> str:
    motivo = estado.get("motivo_manual") or {}
    codigo = motivo.get("codigo")
    incidente = estado.get("incidente_id")
    em_risco = [o["os_id"] for o in estado.get("os_em_risco") or []]
    suspensas = estado.get("os_suspensas") or []
    if codigo == "avaliacao_indisponivel":
        return (
            f"Incidente {incidente}: não consegui avaliar o risco das OS {ids(em_risco)}. "
            "Elas continuam AGENDADAS; verifique a previsão e decida." + _perda_sem_suspensao(estado)
        )
    if codigo == "erp_indisponivel":
        return f"Incidente {incidente}: não consegui ler as OS no ERP ({motivo.get('detalhe')}). Verifique."
    if codigo == "nenhuma_os_suspensa":
        return (
            f"Incidente {incidente}: a avaliação apontou risco, mas nenhuma OS foi suspensa "
            f"({motivo.get('detalhe')})."
        )
    if codigo == "falha_execucao":
        # O Executor parou no meio: parte do plano pode já estar no ERP.
        feito = "; ".join(motivo.get("executados") or []) or "nada"
        return (
            f"Incidente {incidente}: a execução do plano parou no meio. Já feito: {feito}. "
            f"Falhou: {motivo.get('detalhe')}. Confira as OS no painel e decida."
        )
    alvo = f"OS {ids(suspensas)} segue(m) SUSPENSA(S)" if suspensas else "Nenhuma OS foi alterada"
    razoes = {
        "plano_reprovado": "não encontrei uma janela que passe no Validador",
        "janela_invalida": "a janela aprovada deixou de valer",
        "resposta_invalida": "a resposta recebida não foi reconhecida",
    }
    razao = razoes.get(codigo, codigo or "motivo desconhecido")
    return f"Incidente {incidente}: {alvo}; {razao}. Decida no painel."


def _perda_sem_suspensao(estado) -> str:
    """G5: sem suspensão, a demora custa a perda inteira de aplicar com o tempo previsto."""
    perdas = [p for p in estado.get("perdas_sem_suspensao") or [] if p.get("perda_rs") is not None]
    if not perdas:
        return ""
    total = sum(p["perda_rs"] for p in perdas)
    estimativa = (
        " (estimativa)" if any(p["fonte_fator"].lower().startswith("estimativa") for p in perdas) else ""
    )
    tempo = TEMPO_PREVISTO.get(perdas[0]["condicao"], "o tempo previsto")
    return f" Se forem aplicadas como estão, com {tempo}, a perda é de {formatar_reais(total)}{estimativa}."


async def perdas_sem_suspensao(deps: Dependencias, estado) -> list[dict]:
    """Perda de cada OS em risco aplicada sob o primeiro gatilho do Monitor (área × dose × preço × fator)."""
    gatilhos = (estado.get("incidente") or {}).get("gatilhos") or []
    if not gatilhos:
        return []
    condicao = gatilhos[0]["tipo"]
    perdas = []
    for o in estado.get("os_em_risco") or []:
        impacto = await calcular_impacto(OsEmRisco.model_validate(o), condicao, deps.conhecimento)
        perdas.append(
            {
                "os_id": impacto.os_id,
                "condicao": condicao,
                "perda_rs": impacto.perda_rs,
                "fonte_fator": impacto.fonte_fator,
            }
        )
    return perdas


def criar_decisao_manual(deps: Dependencias):
    async def decisao_manual(ctx: Context, node_input):
        incidente_id = ctx.state.get("incidente_id")
        if (ctx.state.get("motivo_manual") or {}).get("codigo") == "avaliacao_indisponivel":
            ctx.state["perdas_sem_suspensao"] = await perdas_sem_suspensao(deps, ctx.state)
        await deps.repo.mudar_status(incidente_id, "aguardando_decisao_manual", de=ABERTOS)
        texto = texto_decisao_manual(ctx.state)
        await alertar(deps, texto)
        await auditar(
            deps,
            ctx,
            "decisao_manual",
            "pedir_decisao_manual",
            depois=ctx.state.get("motivo_manual"),
            justificativa=texto,
        )
        return resumo(texto)

    return decisao_manual


def resumo(texto: str) -> types.Content:
    """Texto final do incidente, visível no `adk web` / `adk run` (a API ignora)."""
    return types.ModelContent(parts=[types.Part.from_text(text=texto)])


def criar_encerrar(deps: Dependencias):
    async def encerrar(ctx: Context, node_input):
        incidente_id = ctx.state.get("incidente_id")
        await deps.repo.mudar_status(incidente_id, "encerrado", de=ABERTOS)
        await auditar(
            deps, ctx, "encerrar", "encerrar_incidente", justificativa=ctx.state.get("motivo_encerramento")
        )
        return resumo(f"Incidente {incidente_id} encerrado: {ctx.state.get('motivo_encerramento')}.")

    return encerrar
