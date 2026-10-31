"""Fins do grafo: `encerrar` e `decisao_manual` (contrato 4)."""

from google.adk import Context
from google.genai import types

from agents.deps import Dependencias
from agents.nodes.comum import alertar, auditar, ids

ABERTOS = {"aberto", "aguardando_aprovacao", "aguardando_decisao_manual"}


def texto_decisao_manual(estado) -> str:
    motivo = estado.get("motivo_manual") or {}
    codigo = motivo.get("codigo")
    incidente = estado.get("incidente_id")
    em_risco = [o["os_id"] for o in estado.get("os_em_risco") or []]
    suspensas = estado.get("os_suspensas") or []
    if codigo == "avaliacao_indisponivel":
        return (
            f"Incidente {incidente}: não consegui avaliar o risco das OS {ids(em_risco)}. "
            "Elas continuam AGENDADAS; verifique a previsão e decida."
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
        "gerente_recusou": "você recusou o plano",
        "opcoes_esgotadas": "as opções de remarcação se esgotaram",
        "janela_invalida": "a janela aprovada deixou de valer",
        "resposta_invalida": "a resposta recebida não foi reconhecida",
    }
    razao = razoes.get(codigo, codigo or "motivo desconhecido")
    return f"Incidente {incidente}: {alvo}; {razao}. Decida no painel."


def criar_decisao_manual(deps: Dependencias):
    async def decisao_manual(ctx: Context, node_input):
        incidente_id = ctx.state.get("incidente_id")
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
