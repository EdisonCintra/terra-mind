"""Textos dos alertas ao gerente (T8.9), para o `agents` passar a `canal.enviar_alerta`.

Cada texto diz o que aconteceu, em que estado a OS ficou e o que o gerente deve fazer.
"""

from typing import Literal

Motivo = Literal["avaliador_falhou", "sem_evidencia", "validador_reprovou", "nao", "opcoes_esgotadas"]

_DECISAO_MANUAL: dict[str, str] = {
    "sem_evidencia": (
        "Sem evidência na bula: a bula não traz o limite de chuva para este produto. "
        "A OS NÃO foi suspensa e segue agendada. Decida manualmente."
    ),
    "avaliador_falhou": (
        "O Avaliador de Risco falhou e não foi possível confirmar a violação. "
        "A OS NÃO foi suspensa e segue agendada. Decida manualmente."
    ),
    "validador_reprovou": (
        "Não encontrei um plano válido de reagendamento (reprovado 2 vezes na validação). "
        "A OS segue suspensa. Decida manualmente."
    ),
    "nao": "Plano recusado. A OS segue suspensa. Decida manualmente.",
    "opcoes_esgotadas": (
        "As opções de reagendamento acabaram sem aprovação. A OS segue suspensa. Decida manualmente."
    ),
}


def alerta_decisao_manual(motivo: Motivo, incidente_id: int, detalhe: str | None = None) -> str:
    texto = f"⚠️ Incidente #{incidente_id} aguarda decisão manual.\n{_DECISAO_MANUAL[motivo]}"
    return f"{texto}\n{detalhe}" if detalhe else texto


def alerta_escalonamento(incidente_id: int, plano_id: int, minutos: int) -> str:
    return (
        f"⏰ Sem resposta há {minutos} min no incidente #{incidente_id} (plano #{plano_id}). "
        "A OS segue suspensa. Responda SIM, NÃO ou Ver opções na mensagem do plano; "
        "um SIM tardio vale se a janela ainda for válida."
    )
