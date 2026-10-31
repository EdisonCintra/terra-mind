"""Grava uma previsão real do Open-Meteo para usar no replay.

uv run python -m monitor.gravar --lat -17.79 --lon -50.92 --saida monitor/scenarios/nome.json
"""

import argparse
import json
from pathlib import Path

import httpx

from monitor.fontes import CAMPOS, OPEN_METEO


def gravar(lat: float, lon: float, saida: Path) -> None:
    params = {"latitude": lat, "longitude": lon, "hourly": ",".join(CAMPOS.values()),
              "forecast_days": 5, "timezone": "America/Sao_Paulo", "wind_speed_unit": "kmh"}
    resposta = httpx.get(OPEN_METEO, params=params, timeout=10)
    resposta.raise_for_status()
    Path(saida).write_text(json.dumps(resposta.json(), ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--lat", type=float, required=True)
    args.add_argument("--lon", type=float, required=True)
    args.add_argument("--saida", type=Path, required=True)
    a = args.parse_args()
    gravar(a.lat, a.lon, a.saida)
