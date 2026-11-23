"""Risco por talhão nas próximas horas: verde, amarelo ou vermelho (contrato 22).

`motivos` é o texto pronto (contrato 22); `detalhes` traz o mesmo motivo em partes (valor, limite, horário),
para a tela montar uma linha legível sem desmontar o texto.
"""

from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel

from monitor.avaliacao import avaliar
from monitor.modelos import Gatilho, HoraPrevisao, Limiares

SP = ZoneInfo("America/Sao_Paulo")
FRACAO_ATENCAO = 0.75  # a partir de 75% do limite o talhão já pede atenção
MARGEM_UMIDADE_PCT = 10  # pontos acima do mínimo

Parametro = Literal["chuva", "vento", "umidade", "rajada", "delta_t", "inversao"]
ROTULO: dict[str, tuple[str, str]] = {
    "chuva": ("Chuva", "mm"), "vento": ("Vento", "km/h"), "umidade": ("Umidade", "%"),
    "rajada": ("Rajada", "km/h"), "delta_t": ("Delta T", "°C"), "inversao": ("Inversão térmica", ""),
}


class Detalhe(BaseModel):
    parametro: Parametro
    rotulo: str
    nivel: Literal["amarelo", "vermelho"]
    valor: float | None = None
    limite: float | None = None
    unidade: str = ""
    # "max": o valor é o pico e passa do limite; "min": o valor é o mínimo e fica abaixo; "total": soma da janela
    sentido: Literal["max", "min", "total", "evento"] = "max"
    inicio: AwareDatetime
    fim: AwareDatetime


class Risco(BaseModel):
    talhao_id: int
    nivel: Literal["verde", "amarelo", "vermelho"]
    motivos: list[str]
    detalhes: list[Detalhe] = []
    ate: AwareDatetime | None = None


def _num(v: float) -> str:
    """Número no formato brasileiro, sem casas à toa: 22.9 → "22,9"; 15.0 → "15"."""
    return f"{round(v, 1):g}".replace(".", ",")


def _quando(ts: datetime) -> str:
    return ts.astimezone(SP).strftime("%d/%m %Hh")


def _motivo_gatilho(g: Gatilho) -> str:
    ate = _quando(g.fim)
    if g.tipo == "inversao":
        return f"inversão térmica prevista até {ate}"
    rotulo, un = ROTULO[g.tipo]
    return f"{rotulo.lower() if g.tipo != 'delta_t' else rotulo} {_num(g.valor)} {un} até {ate}; limite {_num(g.limiar)} {un}"


def _detalhe_gatilho(g: Gatilho) -> Detalhe:
    rotulo, un = ROTULO[g.tipo]
    if g.tipo == "inversao":
        return Detalhe(parametro=g.tipo, rotulo=rotulo, nivel="vermelho", sentido="evento", inicio=g.inicio, fim=g.fim)
    sentido = "total" if g.tipo == "chuva" else "min" if g.tipo == "umidade" else "max"
    return Detalhe(parametro=g.tipo, rotulo=rotulo, nivel="vermelho", valor=round(g.valor, 1),
                   limite=round(g.limiar, 1), unidade=un, sentido=sentido, inicio=g.inicio, fim=g.fim)


def _amarelos(janela: list[HoraPrevisao], lim: Limiares,
              horizonte_h: int) -> tuple[list[str], list[Detalhe], datetime | None]:
    motivos: list[str] = []
    detalhes: list[Detalhe] = []

    def regra(texto: str, horas: list[HoraPrevisao], parametro: str, valor: float, limite: float,
              sentido: str) -> None:
        rotulo, un = ROTULO[parametro]
        motivos.append(texto)
        detalhes.append(Detalhe(parametro=parametro, rotulo=rotulo, nivel="amarelo", valor=round(valor, 1),
                                limite=round(limite, 1), unidade=un, sentido=sentido,
                                inicio=horas[0].ts, fim=horas[-1].ts))

    chuvosas = [h for h in janela if h.chuva_mm is not None and h.chuva_mm > 0]
    total = sum(h.chuva_mm for h in chuvosas)
    if chuvosas and total >= FRACAO_ATENCAO * lim.chuva_mm:
        regra(f"chuva {_num(total)} mm em {horizonte_h} h ({total / lim.chuva_mm:.0%} do limite de "
              f"{_num(lim.chuva_mm)} mm)", chuvosas, "chuva", total, lim.chuva_mm, "total")
    ventosas = [h for h in janela if h.vento_kmh is not None and h.vento_kmh >= FRACAO_ATENCAO * lim.vento_kmh]
    if ventosas:
        pico = max(h.vento_kmh for h in ventosas)
        regra(f"vento até {_num(pico)} km/h (perto do limite de {_num(lim.vento_kmh)} km/h)",
              ventosas, "vento", pico, lim.vento_kmh, "max")
    rajadas = [h for h in janela if h.rajada_kmh is not None and h.rajada_kmh >= FRACAO_ATENCAO * lim.rajada_kmh]
    if rajadas:
        pico = max(h.rajada_kmh for h in rajadas)
        regra(f"rajada até {_num(pico)} km/h (perto do limite de {_num(lim.rajada_kmh)} km/h)",
              rajadas, "rajada", pico, lim.rajada_kmh, "max")
    secas = [h for h in janela
             if h.umidade_pct is not None and h.umidade_pct <= lim.umidade_min_pct + MARGEM_UMIDADE_PCT]
    if secas:
        minima = min(h.umidade_pct for h in secas)
        regra(f"umidade mínima de {_num(minima)}% (perto do limite de {_num(lim.umidade_min_pct)}%)",
              secas, "umidade", minima, lim.umidade_min_pct, "min")
    frios = [h for h in janela if h.delta_t_c is not None and h.delta_t_c < lim.delta_t_min_c]
    if frios:
        minimo = min(h.delta_t_c for h in frios)
        regra(f"Delta T abaixo de {_num(lim.delta_t_min_c)} °C (mínimo {_num(minimo)} °C)",
              frios, "delta_t", minimo, lim.delta_t_min_c, "min")
    return motivos, detalhes, max(d.fim for d in detalhes) if detalhes else None


def classificar(talhao_id: int, horas: list[HoraPrevisao], limiares: Limiares, agora: datetime,
                horizonte_h: int = 48) -> Risco:
    """Verde, amarelo (perto de um limite) ou vermelho (limite estourado) em [agora, agora + horizonte).

    Janela sem nenhuma hora devolve verde; quem chama deve garantir que há dado na janela.
    """
    lim = limiares.model_copy(update={"janela_h": horizonte_h})
    gatilhos = avaliar(horas, lim, agora)
    if gatilhos:
        return Risco(talhao_id=talhao_id, nivel="vermelho", motivos=[_motivo_gatilho(g) for g in gatilhos],
                     detalhes=[_detalhe_gatilho(g) for g in gatilhos], ate=max(g.fim for g in gatilhos))
    janela = [h for h in horas if agora <= h.ts < agora + timedelta(hours=horizonte_h)]
    motivos, detalhes, ate = _amarelos(janela, lim, horizonte_h)
    if motivos:
        return Risco(talhao_id=talhao_id, nivel="amarelo", motivos=motivos, detalhes=detalhes, ate=ate)
    return Risco(talhao_id=talhao_id, nivel="verde", motivos=[], ate=None)
