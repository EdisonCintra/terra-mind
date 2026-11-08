"""Próximas janelas válidas de aplicação (decisão A6: código do Gustavo, fora do MCP).

Um início `t` é válido quando:
- é hora cheia, começa entre `HORA_MIN` e `HORA_MAX` (horário local) e está a pelo menos
  `ANTECEDENCIA` de `agora`;
- o parâmetro violado respeita o limite citado pelo Avaliador no intervalo em que o limite vale:
  chuva somada em `[t, t + janela_h)` ≤ limite; nos outros parâmetros, o pico fica ≤ limite máximo
  ou o vale fica ≥ limite mínimo (`ViolacaoOs.sentido_efetivo`), em `[t, t + duracao)` ou
  `[t, t + janela_h)`, o que for maior. Inversão térmica: nenhuma hora com inversão no intervalo;
- o intervalo inteiro cabe na previsão (não extrapola além do snapshot de 120 h) e traz o dado do
  parâmetro em todas as horas: sem Delta T, rajada ou inversão do Monitor (contrato 21, opcionais), a
  janela não é aceita, porque não há como mostrar que ela respeita o limite.

A disponibilidade da máquina é checada à parte (`agents.rules.validador`), porque exige o ERP.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from agents.modelos import HoraPrevisao, ViolacaoOs

FUSO = ZoneInfo("America/Sao_Paulo")
HORA_MIN = 6
HORA_MAX = 18
ANTECEDENCIA = timedelta(hours=2)


def _inversao(h: HoraPrevisao) -> float | None:
    return None if h.inversao is None else float(h.inversao)


# Parâmetro (exceto chuva, que é somada) → (valor da hora, nome na mensagem, unidade).
SERIES: dict[str, tuple[Callable[[HoraPrevisao], float | None], str, str]] = {
    "vento": (lambda h: h.vento_kmh, "vento", " km/h"),
    "umidade": (lambda h: h.umidade_pct, "umidade", "%"),
    "temperatura": (lambda h: h.temp_c, "temperatura", " °C"),
    "rajada": (lambda h: h.rajada_kmh, "rajada", " km/h"),
    "delta_t": (lambda h: h.delta_t_c, "Delta T", " °C"),
    "inversao": (_inversao, "inversão térmica", ""),
}


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
        return None
    valor_da_hora, nome, unidade = SERIES[violacao.parametro]
    valores = [valor_da_hora(h) for h in trecho]
    if any(v is None for v in valores):
        return f"sem previsão de {nome} para o intervalo"
    if violacao.parametro == "inversao":
        return "inversão térmica prevista no intervalo" if max(valores) > limite else None
    if violacao.sentido_efetivo == "minimo":
        vale = min(valores)
        if vale < limite:
            return f"{nome} de {vale:.0f}{unidade} abaixo do mínimo de {limite:g}{unidade}"
        return None
    pico = max(valores)
    if pico > limite:
        return f"{nome} de {pico:.0f}{unidade} acima do limite de {limite:g}{unidade}"
    return None


def blocos(inicios: list[datetime]) -> list[list[datetime]]:
    """Agrupa inícios válidos em blocos de horas seguidas (cada bloco é uma "janela" para o gerente)."""
    resultado: list[list[datetime]] = []
    for t in sorted(inicios):
        if resultado and t - resultado[-1][-1] == timedelta(hours=1):
            resultado[-1].append(t)
        else:
            resultado.append([t])
    return resultado


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
