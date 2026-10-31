"""Resumo da previsão para os prompts: o LLM recebe números agregados em código, não a série crua."""

from datetime import datetime, timedelta

from agents.modelos import HoraPrevisao
from agents.rules.janelas import FUSO

HORAS_APOS_FIM = (1, 2, 4, 6, 12, 24)


def _agregar(trecho: list[HoraPrevisao]) -> dict:
    return {
        "chuva_mm": round(sum(h.chuva_mm for h in trecho), 1),
        "vento_max_kmh": round(max(h.vento_kmh for h in trecho), 1),
        "umidade_min_pct": round(min(h.umidade_pct for h in trecho), 1),
        "temp_max_c": round(max(h.temp_c for h in trecho), 1),
    }


def previsao_da_os(horas: list[HoraPrevisao], inicio: datetime, duracao_h: float) -> dict:
    """A previsão no horário de uma OS: extremos durante a aplicação e a chuva acumulada depois do fim.

    Com blocos fixos o LLM comparava o limite com horas fora da aplicação (vento das 03 h para uma
    pulverização das 05 h às 13 h).
    """
    fim = inicio + timedelta(hours=duracao_h)
    durante = [h for h in horas if inicio <= h.ts < fim]
    return {
        "aplicacao": {
            "de": inicio.astimezone(FUSO).strftime("%d/%m %H:%M"),
            "ate": fim.astimezone(FUSO).strftime("%d/%m %H:%M"),
            **(_agregar(durante) if durante else {"sem_previsao": True}),
        },
        "chuva_acumulada_apos_fim_mm": {
            f"{n}h": round(sum(h.chuva_mm for h in horas if fim <= h.ts < fim + timedelta(hours=n)), 1)
            for n in HORAS_APOS_FIM
        },
    }


def resumir(
    horas: list[HoraPrevisao], inicio: datetime, horas_total: int = 120, bloco_h: int = 6
) -> list[dict]:
    """Blocos de `bloco_h` horas: chuva somada, vento máximo, umidade mínima e temperatura máxima."""
    blocos = []
    for i in range(0, horas_total, bloco_h):
        a, b = inicio + timedelta(hours=i), inicio + timedelta(hours=i + bloco_h)
        trecho = [h for h in horas if a <= h.ts < b]
        if not trecho:
            continue
        blocos.append(
            {
                "de": a.astimezone(FUSO).strftime("%d/%m %H:%M"),
                "ate": b.astimezone(FUSO).strftime("%d/%m %H:%M"),
                **_agregar(trecho),
            }
        )
    return blocos
