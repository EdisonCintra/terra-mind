from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from monitor.modelos import HoraPrevisao

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
CAMPOS = {"chuva_mm": "precipitation", "vento_kmh": "wind_speed_10m",
          "umidade_pct": "relative_humidity_2m", "temp_c": "temperature_2m"}


class FontePrevisaoIndisponivel(Exception):
    pass


class FontePrevisao(Protocol):
    def obter(self, lat: float, lon: float, agora: datetime) -> list[HoraPrevisao]: ...


def parse_open_meteo(payload: dict) -> list[HoraPrevisao]:
    """Converte a resposta horária do Open-Meteo; horários no fuso devolvido pela API."""
    fuso = ZoneInfo(payload["timezone"])
    h = payload["hourly"]
    return [
        HoraPrevisao(ts=datetime.fromisoformat(ts).replace(tzinfo=fuso),
                     **{campo: h[chave][i] or 0.0 for campo, chave in CAMPOS.items()})
        for i, ts in enumerate(h["time"])
    ]


class OpenMeteoFonte:
    def __init__(self, http: httpx.Client):
        self.http = http

    def obter(self, lat: float, lon: float, agora: datetime) -> list[HoraPrevisao]:
        params = {"latitude": lat, "longitude": lon, "hourly": ",".join(CAMPOS.values()),
                  "forecast_days": 5, "timezone": "America/Sao_Paulo", "wind_speed_unit": "kmh"}
        try:
            resposta = self.http.get(OPEN_METEO, params=params, timeout=10)
            resposta.raise_for_status()
        except httpx.HTTPError as erro:
            raise FontePrevisaoIndisponivel(str(erro)) from erro
        return parse_open_meteo(resposta.json())


class ReplayFonte:
    """Previsão gravada (formato Open-Meteo), deslocada para começar na hora atual."""

    def __init__(self, caminho):
        import json
        from pathlib import Path

        self.horas = parse_open_meteo(json.loads(Path(caminho).read_text(encoding="utf-8")))

    def obter(self, lat: float, lon: float, agora: datetime) -> list[HoraPrevisao]:
        if not self.horas:
            return []
        delta = agora.replace(minute=0, second=0, microsecond=0) - self.horas[0].ts
        return [h.model_copy(update={"ts": h.ts + delta}) for h in self.horas]
