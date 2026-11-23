"""Clima observado: leituras de estação (a registrar em `clima.estacoes`) ou reanálise Open-Meteo."""

import logging
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from monitor.modelos import Estacao, LeituraObservada

# o httpx loga a URL completa em INFO; mantém fora do log, como em fontes.py
logging.getLogger("httpx").setLevel(logging.WARNING)

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
CAMPOS_ARQUIVO = {"chuva_mm": "precipitation", "temp_c": "temperature_2m", "umidade_pct": "relative_humidity_2m",
                  "vento_kmh": "wind_speed_10m", "vento_dir_graus": "wind_direction_10m"}
RAIO_TERRA_KM = 6371.0


class FonteObservadaIndisponivel(Exception):
    pass


class FonteObservada(Protocol):
    def leituras(self, lat: float, lon: float, inicio: datetime, fim: datetime) -> list[LeituraObservada]: ...


def distancia_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância de círculo máximo (haversine)."""
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * RAIO_TERRA_KM * asin(sqrt(a))


def estacao_mais_proxima(lat: float, lon: float, estacoes: list[Estacao],
                         raio_km: float = 50) -> tuple[Estacao, float] | None:
    """Estação mais próxima dentro do raio, com a distância em km; fora do raio, None."""
    candidatas = [(e, distancia_km(lat, lon, e.lat, e.lon)) for e in estacoes]
    candidatas = [c for c in candidatas if c[1] <= raio_km]
    return min(candidatas, key=lambda c: c[1]) if candidatas else None


def _valor(serie: dict, chave: str, i: int):
    lista = serie.get(chave)
    return lista[i] if lista is not None and i < len(lista) else None


def parse_reanalise(payload: dict) -> list[LeituraObservada]:
    """Uma leitura por hora da resposta do arquivo Open-Meteo; null vira None (nunca 0)."""
    fuso = ZoneInfo(payload["timezone"])
    h = payload["hourly"]
    return [LeituraObservada(ts=datetime.fromisoformat(ts).replace(tzinfo=fuso), fonte="reanalise",
                             **{campo: _valor(h, chave, i) for campo, chave in CAMPOS_ARQUIVO.items()})
            for i, ts in enumerate(h["time"])]


class ReanaliseOpenMeteo:
    """Reanálise horária do Open-Meteo: atrasa alguns dias e devolve null nas horas mais recentes."""

    def __init__(self, http: httpx.Client):
        self.http = http

    def leituras(self, lat: float, lon: float, inicio: datetime, fim: datetime) -> list[LeituraObservada]:
        params = {"latitude": lat, "longitude": lon, "hourly": ",".join(CAMPOS_ARQUIVO.values()),
                  "start_date": inicio.date().isoformat(), "end_date": fim.date().isoformat(),
                  "wind_speed_unit": "kmh", "timezone": "America/Sao_Paulo"}
        try:
            resposta = self.http.get(OPEN_METEO_ARCHIVE, params=params, timeout=15)
            resposta.raise_for_status()
        except httpx.HTTPError as erro:
            # sem a URL e sem a causa encadeada, que também a mostraria
            if isinstance(erro, httpx.HTTPStatusError):
                motivo = f"Open-Meteo arquivo HTTP {erro.response.status_code}"
            else:
                motivo = f"Open-Meteo arquivo indisponível ({type(erro).__name__})"
            raise FonteObservadaIndisponivel(motivo) from None
        return [l for l in parse_reanalise(resposta.json()) if inicio <= l.ts <= fim]


def ultima_com_dado(leituras: list[LeituraObservada]) -> LeituraObservada | None:
    """Última hora com ao menos um campo medido (as mais recentes da reanálise costumam vir vazias)."""
    campos = tuple(CAMPOS_ARQUIVO)
    for leitura in sorted(leituras, key=lambda l: l.ts, reverse=True):
        if any(getattr(leitura, c) is not None for c in campos):
            return leitura
    return None


def coletar_observacoes(repo, fonte: FonteObservada | None, agora: datetime, horas: int = 24) -> int:
    """Grava as últimas `horas` de cada estação registrada; sem estação ou sem fonte de estação, nada a fazer."""
    if fonte is None:
        return 0
    estacoes = repo.estacoes()
    gravadas = 0
    for estacao in estacoes:
        leituras = fonte.leituras(estacao.lat, estacao.lon, agora - timedelta(hours=horas), agora)
        gravadas += repo.gravar_observacoes(estacao.codigo, leituras)
    return gravadas
