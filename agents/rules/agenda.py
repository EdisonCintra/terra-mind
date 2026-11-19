"""G2: horas boas e ruins para aplicar um produto num talhão, por dia. Tudo em código.

Um início é bom quando uma aplicação de `duracao_h` começando nele respeita todos os limites do
produto (`agents.rules.limites`) sobre a previsão. Inícios seguidos com o mesmo resultado viram uma
faixa ("06h–09h: boa", "10h–18h: vento acima do limite"). A faixa ruim leva o limite que a reprovou,
para a tela mostrar o motivo e a página da bula.
"""

from datetime import date, datetime, timedelta

from pydantic import AwareDatetime, BaseModel

from agents.modelos import HoraPrevisao, LimiteProduto
from agents.rules.janelas import ANTECEDENCIA, FUSO, HORA_MAX, HORA_MIN, motivo_clima
from agents.rules.limites import fonte_do_limite

HORIZONTE = "janela ultrapassa o horizonte da previsão"


class Faixa(BaseModel):
    inicio: AwareDatetime  # primeiro início da faixa
    fim: AwareDatetime  # último início da faixa + 1 h
    serve: bool
    motivo: str | None = None
    parametro: str | None = None
    fonte: str | None = None  # "bula, p. 7" ou "referência genérica"
    pagina: int | None = None
    fonte_doc: str | None = None


class DiaAgenda(BaseModel):
    data: date
    horas_boas: int
    faixas: list[Faixa]


def reprovacao(
    t: datetime, horas: list[HoraPrevisao], limites: list[LimiteProduto], duracao_h: float
) -> tuple[str, LimiteProduto] | None:
    """(motivo, limite) do primeiro limite que uma aplicação iniciada em `t` desrespeita, ou `None`."""
    for limite in limites:
        motivo = motivo_clima(t, horas, limite, duracao_h)
        if motivo:
            return motivo, limite
    return None


def _faixa(t: datetime, horas, limites, duracao_h, agora: datetime) -> Faixa | None:
    """A faixa de uma hora só. `None` quando a previsão não alcança a aplicação (fica fora da agenda)."""
    fim = t + timedelta(hours=1)
    if t < agora + ANTECEDENCIA:
        return Faixa(inicio=t, fim=fim, serve=False, motivo="horário já passou", parametro="horario")
    reprovado = reprovacao(t, horas, limites, duracao_h)
    if reprovado is None:
        return Faixa(inicio=t, fim=fim, serve=True)
    motivo, limite = reprovado
    if motivo == HORIZONTE:
        return None
    return Faixa(
        inicio=t,
        fim=fim,
        serve=False,
        motivo=motivo,
        parametro=f"{limite.parametro}:{limite.sentido_efetivo}",
        fonte=fonte_do_limite(limite),
        pagina=None if limite.generico else limite.pagina,
        fonte_doc=None if limite.generico else limite.fonte_doc,
    )


def agenda_do_talhao(
    horas: list[HoraPrevisao],
    limites: list[LimiteProduto],
    duracao_h: float,
    agora: datetime,
    inicio: date,
    dias: int,
) -> list[DiaAgenda]:
    """Um `DiaAgenda` por dia com previsão, de `inicio` em diante, só no horário de trabalho."""
    por_dia: dict[date, list[Faixa]] = {}
    ultimo = inicio + timedelta(days=dias - 1)
    for t in sorted(h.ts for h in horas):
        local = t.astimezone(FUSO)
        if not (inicio <= local.date() <= ultimo and HORA_MIN <= local.hour <= HORA_MAX) or local.minute:
            continue
        nova = _faixa(t, horas, limites, duracao_h, agora)
        if nova is None:
            continue
        faixas = por_dia.setdefault(local.date(), [])
        anterior = faixas[-1] if faixas else None
        # Mesma situação da hora anterior: estende a faixa (o motivo fica o da primeira hora).
        if (
            anterior
            and anterior.fim == t
            and (anterior.serve, anterior.parametro) == (nova.serve, nova.parametro)
        ):
            anterior.fim = nova.fim
        else:
            faixas.append(nova)
    return [
        DiaAgenda(
            data=dia,
            horas_boas=sum(int((f.fim - f.inicio).total_seconds() // 3600) for f in faixas if f.serve),
            faixas=faixas,
        )
        for dia, faixas in sorted(por_dia.items())
    ]
