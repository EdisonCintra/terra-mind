"""Mensagem ao gerente: o texto do Supervisor + um rodapé montado em código.

Todo número do rodapé (perda em R$, data, máquina, página) vem do estado, nunca do texto do LLM.
"""

from agents.modelos import Impacto, OsEmRisco, PlanoProposto, ViolacaoOs
from agents.rules.impacto import formatar_reais
from agents.rules.janelas import formatar_data

LIMITE_TRECHO = 220


def _trecho(texto: str | None) -> str:
    if not texto:
        return ""
    texto = " ".join(texto.split())
    return texto if len(texto) <= LIMITE_TRECHO else texto[: LIMITE_TRECHO - 1] + "…"


def compor_mensagem(
    plano: PlanoProposto,
    os_por_id: dict[int, OsEmRisco],
    violacoes_por_os: dict[int, list[ViolacaoOs]],
    impactos: dict[int, Impacto],
) -> str:
    linhas = [plano.mensagem.strip(), "", "— Dados conferidos pelo sistema —"]
    for opcao in plano.opcoes:
        os = os_por_id[opcao.os_id]
        impacto = impactos.get(opcao.os_id)
        linhas.append(
            f"OS {os.os_id} · {os.tipo_operacao} · {os.talhao} · {os.produto or 'sem insumo'}: SUSPENSA."
        )
        linhas.append(f"  Proposta: {formatar_data(opcao.novo_inicio)}, máquina {opcao.maquina_id}.")
        if impacto is not None:
            fator = "" if impacto.fator is None else f" (fator {impacto.fator:g}; {impacto.fonte_fator})"
            linhas.append(f"  Perda evitada: {formatar_reais(impacto.perda_rs)}{fator}.")
        for violacao in violacoes_por_os.get(opcao.os_id, []):
            linhas.append(
                f"  Fonte: {violacao.fonte_doc}, p. {violacao.pagina}: “{_trecho(violacao.trecho)}”"
            )
    linhas.append("")
    linhas.append("Responda SIM para aplicar, NÃO para decidir manualmente ou Ver opções para outra data.")
    return "\n".join(linhas)
