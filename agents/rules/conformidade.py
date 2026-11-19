"""G7: o clima durante a aplicação e a conferência dele contra os limites do produto. Tudo em código.

É a prova de que a aplicação respeitou a bula: para cada limite (com a página), o que o clima
registrou no intervalo real da aplicação. Limite genérico entra como referência, sem pesar no veredito.
"""

import math
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from agents.modelos import HoraPrevisao, LimiteProduto
from agents.rules.agenda import HORIZONTE
from agents.rules.janelas import SERIES, motivo_clima
from agents.rules.limites import fonte_do_limite

PONTOS = ["N", "NE", "L", "SE", "S", "SO", "O", "NO"]
FEMININOS = {"temperatura", "umidade", "rajada"}
SEM_DADO = ("sem previsão", HORIZONTE, "avaliação sem")
NOMES = {"chuva": "Chuva", **{p: nome[0].upper() + nome[1:] for p, (_, nome, _) in SERIES.items()}}


class ItemConformidade(BaseModel):
    regra: str  # "Vento máximo de 10 km/h"
    fonte: str  # "bula, p. 7" ou "referência genérica"
    da_bula: bool
    situacao: Literal["conforme", "nao_conforme", "sem_dado"]
    detalhe: str | None = None
    previsto: bool = False  # o intervalo do limite ainda não terminou: o dado é previsão


def hora_cheia(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


def intervalo_em_horas(inicio: datetime, fim: datetime) -> tuple[datetime, float]:
    """A aplicação em horas cheias, como a previsão: (início arredondado para baixo, duração para cima)."""
    t = hora_cheia(inicio)
    return t, float(max(1, math.ceil((fim - t).total_seconds() / 3600)))


def descrever_regra(limite: LimiteProduto) -> str:
    nome = NOMES.get(limite.parametro, limite.parametro)
    if limite.parametro == "inversao":
        return "Sem inversão térmica"
    if limite.parametro == "chuva":
        apos = f" na aplicação e nas {limite.janela_h:g} h seguintes" if limite.janela_h else " na aplicação"
        return f"Chuva de no máximo {limite.limite:g} mm{apos}"
    unidade = limite.unidade or ""
    separador = "" if unidade == "%" else " "
    tipo = "mínim" if limite.sentido_efetivo == "minimo" else "máxim"
    tipo += "a" if limite.parametro in FEMININOS else "o"
    return f"{nome} {tipo} de {limite.limite:g}{separador}{unidade}".strip()


def conferir_aplicacao(
    horas: list[HoraPrevisao], limites: list[LimiteProduto], inicio: datetime, fim: datetime, agora: datetime
) -> list[ItemConformidade]:
    t, duracao_h = intervalo_em_horas(inicio, fim)
    itens = []
    for limite in limites:
        motivo = motivo_clima(t, horas, limite, duracao_h)
        if motivo is None:
            situacao = "conforme"
        elif motivo.startswith(SEM_DADO):
            situacao = "sem_dado"
        else:
            situacao = "nao_conforme"
        vale_ate = t + timedelta(hours=duracao_h + (limite.janela_h or 0))
        previsto = vale_ate > agora and situacao != "sem_dado"
        if motivo and not previsto:
            motivo = motivo.replace("chuva prevista", "chuva registrada")  # o período já passou
        itens.append(
            ItemConformidade(
                regra=descrever_regra(limite),
                fonte=fonte_do_limite(limite),
                da_bula=not limite.generico,
                situacao=situacao,
                detalhe=motivo,
                previsto=previsto,
            )
        )
    return itens


def veredito(itens: list[ItemConformidade]) -> bool | None:
    """`False` se algum limite da bula foi desrespeitado, `True` se todos os da bula foram conferidos e
    respeitados, e `None` quando não dá para afirmar (bula não lida ou clima sem dado)."""
    da_bula = [i for i in itens if i.da_bula]
    if any(i.situacao == "nao_conforme" for i in da_bula):
        return False
    if da_bula and all(i.situacao == "conforme" for i in da_bula):
        return True
    return None


def _direcao(graus: list[float]) -> str | None:
    """Direção predominante (média circular) como ponto cardeal: de onde o vento vem."""
    if not graus:
        return None
    seno = sum(math.sin(math.radians(g)) for g in graus)
    cosseno = sum(math.cos(math.radians(g)) for g in graus)
    media = math.degrees(math.atan2(seno, cosseno)) % 360
    return PONTOS[round(media / 45) % 8]


def _leitura(h: HoraPrevisao) -> dict:
    return {"temp_c": h.temp_c, "umidade_pct": h.umidade_pct, "vento_kmh": h.vento_kmh}


def resumo_clima(horas: list[HoraPrevisao], inicio: datetime, fim: datetime) -> dict | None:
    """Extremos durante a aplicação e as leituras do início e do fim (a IN MAPA 2/2008, art. 9º, XIV,
    pede as duas). `None` quando não há dado para o intervalo."""
    t, duracao_h = intervalo_em_horas(inicio, fim)
    trecho = sorted((h for h in horas if t <= h.ts < t + timedelta(hours=duracao_h)), key=lambda h: h.ts)
    if not trecho:
        return None
    rajadas = [h.rajada_kmh for h in trecho if h.rajada_kmh is not None]
    deltas = [h.delta_t_c for h in trecho if h.delta_t_c is not None]
    return {
        "horas": len(trecho),
        "temp_min_c": min(h.temp_c for h in trecho),
        "temp_max_c": max(h.temp_c for h in trecho),
        "umidade_min_pct": min(h.umidade_pct for h in trecho),
        "umidade_max_pct": max(h.umidade_pct for h in trecho),
        "vento_min_kmh": min(h.vento_kmh for h in trecho),
        "vento_max_kmh": max(h.vento_kmh for h in trecho),
        "vento_direcao": _direcao([h.vento_dir_graus for h in trecho if h.vento_dir_graus is not None]),
        "rajada_max_kmh": max(rajadas) if rajadas else None,
        "delta_t_min_c": min(deltas) if deltas else None,
        "delta_t_max_c": max(deltas) if deltas else None,
        "chuva_mm": round(sum(h.chuva_mm for h in trecho), 1),
        "inicial": _leitura(trecho[0]),
        "final": _leitura(trecho[-1]),
    }
