"""Resumo da previsão para os prompts: o LLM recebe números agregados em código, não a série crua."""

from datetime import datetime, timedelta

from agents.modelos import HoraPrevisao
from agents.rules.janelas import FUSO


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
                "chuva_mm": round(sum(h.chuva_mm for h in trecho), 1),
                "vento_max_kmh": round(max(h.vento_kmh for h in trecho), 1),
                "umidade_min_pct": round(min(h.umidade_pct for h in trecho), 1),
                "temp_max_c": round(max(h.temp_c for h in trecho), 1),
            }
        )
    return blocos
