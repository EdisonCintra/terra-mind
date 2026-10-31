"""Próximas janelas válidas de aplicação (decisão A6: código do Gustavo, fora do MCP).

Um início `t` é válido quando:
- é hora cheia, começa entre `HORA_MIN` e `HORA_MAX` (horário local) e está a pelo menos
  `ANTECEDENCIA` de `agora`;
- o parâmetro violado respeita o limite citado pelo Avaliador no intervalo em que o limite vale:
  chuva somada em `[t, t + janela_h)` ≤ limite; vento e temperatura (máximos) ≤ limite; umidade
  (mínima) ≥ limite, todos em `[t, t + duracao)` ou `[t, t + janela_h)`, o que for maior;
- o intervalo inteiro cabe na previsão (não extrapola além do snapshot de 120 h).

A disponibilidade da máquina é checada à parte (`agents.rules.validador`), porque exige o ERP.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from agents.modelos import HoraPrevisao, ViolacaoOs

FUSO = ZoneInfo("America/Sao_Paulo")
HORA_MIN = 6
HORA_MAX = 18
ANTECEDENCIA = timedelta(hours=2)


def formatar_data(t: datetime) -> str:
    """Data como aparece para o gerente e o operador: "31/10 às 06:00"."""
    return f"{t.astimezone(FUSO):%d/%m às %H:%M}"


def _janela_h(violacao: ViolacaoOs, duracao_h: float) -> float:
    """Horas a partir do início em que o limite vale.

    Chuva: durante a aplicação e nas `janela_h` horas depois do fim dela ("não chover 24 h após a
    aplicação" numa OS de 8 h → 32 h). Vento, umidade e temperatura: durante a aplicação, ou por
    `janela_h` se o documento pedir mais.
    """
    if violacao.parametro == "chuva":
        return duracao_h + (violacao.janela_h or 0)
    return max(duracao_h, violacao.janela_h or 0)


def motivo_invalida(
    t: datetime,
    horas: list[HoraPrevisao],
    violacoes: ViolacaoOs | list[ViolacaoOs],
    duracao_h: float,
    agora: datetime,
) -> str | None:
    """Devolve por que `t` não serve, ou `None` se a janela é válida (sem checar máquina).

    Uma OS pode violar mais de um limite (chuva e vento, por exemplo): `t` precisa respeitar todos.
    """
    for violacao in violacoes if isinstance(violacoes, list) else [violacoes]:
        motivo = _motivo_um_limite(t, horas, violacao, duracao_h, agora)
        if motivo:
            return motivo
    return None


def _motivo_um_limite(
    t: datetime, horas: list[HoraPrevisao], violacao: ViolacaoOs, duracao_h: float, agora: datetime
) -> str | None:
    local = t.astimezone(FUSO)
    if local.minute or local.second:
        return "início fora de hora cheia"
    if not HORA_MIN <= local.hour <= HORA_MAX:
        return f"início às {local:%H:%M}, fora do horário de trabalho ({HORA_MIN}h–{HORA_MAX}h)"
    if t < agora + ANTECEDENCIA:
        return "início antes da antecedência mínima de 2 h"
    if violacao.parametro is None or violacao.limite is None:
        return "avaliação sem parâmetro ou limite"
    fim = t + timedelta(hours=_janela_h(violacao, duracao_h))
    if not horas or max(h.ts for h in horas) + timedelta(hours=1) < fim:
        return "janela ultrapassa o horizonte da previsão"
    trecho = [h for h in horas if t <= h.ts < fim]
    if not trecho:
        return "sem previsão para o intervalo"
    limite = violacao.limite
    if violacao.parametro == "chuva":
        total = sum(h.chuva_mm for h in trecho)
        if total > limite:
            return f"chuva prevista de {total:.1f} mm acima do limite de {limite:g} mm"
    elif violacao.parametro == "vento":
        pico = max(h.vento_kmh for h in trecho)
        if pico > limite:
            return f"vento de {pico:.0f} km/h acima do limite de {limite:g} km/h"
    elif violacao.parametro == "umidade":
        minimo = min(h.umidade_pct for h in trecho)
        if minimo < limite:
            return f"umidade de {minimo:.0f}% abaixo do mínimo de {limite:g}%"
    elif violacao.parametro == "temperatura":
        pico = max(h.temp_c for h in trecho)
        if pico > limite:
            return f"temperatura de {pico:.0f} °C acima do limite de {limite:g} °C"
    return None


def candidatos(horas: list[HoraPrevisao], agora: datetime) -> list[datetime]:
    """Horas cheias da previsão a partir da antecedência mínima, em ordem."""
    return sorted(h.ts for h in horas if h.ts >= agora + ANTECEDENCIA and h.ts.minute == 0)


def janelas_climaticas(
    horas: list[HoraPrevisao],
    violacoes: ViolacaoOs | list[ViolacaoOs],
    duracao_h: float,
    agora: datetime,
    excluir: set[datetime] | None = None,
) -> list[datetime]:
    """Todos os inícios que passam no clima, em ordem, sem os já oferecidos ao gerente."""
    excluir = excluir or set()
    return [
        t
        for t in candidatos(horas, agora)
        if t not in excluir and motivo_invalida(t, horas, violacoes, duracao_h, agora) is None
    ]
