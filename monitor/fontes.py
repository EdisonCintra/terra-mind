import json
import math
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from monitor.modelos import HoraPrevisao

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
CAMPOS = {"chuva_mm": "precipitation", "vento_kmh": "wind_speed_10m",
          "umidade_pct": "relative_humidity_2m", "temp_c": "temperature_2m"}


def parametros_open_meteo(lat: float, lon: float) -> dict:
    return {"latitude": lat, "longitude": lon, "hourly": ",".join(CAMPOS.values()),
            "forecast_days": 5, "timezone": "America/Sao_Paulo", "wind_speed_unit": "kmh"}


# Mesmo cálculo de `agents.clientes.clima.vento_2m`.
# O Open-Meteo dá o vento a 10 m; a bula e o anemômetro do operador falam do vento na altura da
# barra, perto de 2 m, onde venta menos. Perfil logarítmico da FAO-56 (eq. 47):
#     u2 = u10 × 4,87 / ln(67,8 × 10 − 5,42) ≈ u10 × 0,748
FATOR_VENTO_2M = 4.87 / math.log(67.8 * 10 - 5.42)


def vento_2m(vento_10m: float) -> float:
    """Vento (ou rajada) medido a 10 m convertido para 2 m, em km/h, com uma casa decimal."""
    return round(vento_10m * FATOR_VENTO_2M, 1)


class FontePrevisaoIndisponivel(Exception):
    pass


class FontePrevisao(Protocol):
    def obter(self, lat: float, lon: float, agora: datetime) -> list[HoraPrevisao]: ...


def parse_open_meteo(payload: dict) -> list[HoraPrevisao]:
    """Converte a resposta horária do Open-Meteo; horários no fuso devolvido pela API e vento a 2 m."""
    fuso = ZoneInfo(payload["timezone"])
    h = payload["hourly"]
    horas = []
    for i, ts in enumerate(h["time"]):
        valores = {campo: h[chave][i] or 0.0 for campo, chave in CAMPOS.items()}
        valores["vento_kmh"] = vento_2m(valores["vento_kmh"])
        horas.append(HoraPrevisao(ts=datetime.fromisoformat(ts).replace(tzinfo=fuso), **valores))
    return horas


class OpenMeteoFonte:
    def __init__(self, http: httpx.Client):
        self.http = http

    def obter(self, lat: float, lon: float, agora: datetime) -> list[HoraPrevisao]:
        try:
            resposta = self.http.get(OPEN_METEO, params=parametros_open_meteo(lat, lon), timeout=10)
            resposta.raise_for_status()
        except httpx.HTTPError as erro:
            raise FontePrevisaoIndisponivel(str(erro)) from erro
        return parse_open_meteo(resposta.json())


class ReplayFonte:
    """Previsão gravada (formato Open-Meteo), deslocada para começar na hora atual."""

    def __init__(self, caminho):
        self.horas = parse_open_meteo(json.loads(Path(caminho).read_text(encoding="utf-8")))

    def obter(self, lat: float, lon: float, agora: datetime) -> list[HoraPrevisao]:
        if not self.horas:
            return []
        delta = agora.replace(minute=0, second=0, microsecond=0) - self.horas[0].ts
        return [h.model_copy(update={"ts": h.ts + delta}) for h in self.horas]
