import json
import logging
import math
from datetime import date, datetime
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from monitor.clima import delta_t, inversao_provavel
from monitor.modelos import DiaPrevisao, HoraPrevisao, Limiares, Previsao

# o httpx loga a URL completa em INFO, e com plano pago ela carrega o `apikey`
logging.getLogger("httpx").setLevel(logging.WARNING)

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_CUSTOMER = "https://customer-api.open-meteo.com/v1/forecast"  # plano pago, com `apikey`
CAMPOS = {"chuva_mm": "precipitation", "vento_kmh": "wind_speed_10m",
          "umidade_pct": "relative_humidity_2m", "temp_c": "temperature_2m"}
# só na requisição: 80/120 m e nuvem alimentam a inversão e são descartados no parse
CAMPOS_EXTRAS = ("wind_direction_10m", "wind_gusts_10m", "temperature_80m", "temperature_120m",
                 "cloud_cover", "is_day")
CAMPOS_DIARIOS = ("precipitation_sum", "precipitation_probability_max", "temperature_2m_min",
                  "temperature_2m_max", "wind_speed_10m_max", "wind_gusts_10m_max",
                  "wind_direction_10m_dominant")
HORAS_PREVISAO = 120


def parametros_open_meteo(lat: float, lon: float) -> dict:
    return {"latitude": lat, "longitude": lon,
            "hourly": ",".join([*CAMPOS.values(), *CAMPOS_EXTRAS]), "daily": ",".join(CAMPOS_DIARIOS),
            "forecast_days": 16, "timezone": "America/Sao_Paulo", "wind_speed_unit": "kmh"}


def chave_grade(lat: float, lon: float) -> tuple[float, float]:
    """Ponto de grade da consulta: talhões a ~1 km um do outro dividem a mesma previsão."""
    return round(lat, 2), round(lon, 2)


# Mesmo cálculo de `agents.clientes.clima.vento_2m`.
# O Open-Meteo dá o vento a 10 m; a bula e o anemômetro do operador falam do vento na altura da
# barra, perto de 2 m, onde venta menos. Perfil logarítmico da FAO-56 (eq. 47):
#     u2 = u10 × 4,87 / ln(67,8 × 10 − 5,42) ≈ u10 × 0,748
FATOR_VENTO_2M = 4.87 / math.log(67.8 * 10 - 5.42)


def vento_2m(vento_10m: float) -> float:
    """Vento (ou rajada) medido a 10 m convertido para 2 m, em km/h, com uma casa decimal."""
    return round(vento_10m * FATOR_VENTO_2M, 1)


def _a_2m(vento_10m: float | None) -> float | None:
    """`vento_2m` que deixa o dado ausente como None (contrato 21)."""
    return None if vento_10m is None else vento_2m(vento_10m)


class FontePrevisaoIndisponivel(Exception):
    pass


class FontePrevisao(Protocol):
    nome: str  # gravado em clima.previsoes.fonte

    def obter(self, lat: float, lon: float, agora: datetime) -> Previsao: ...


def _valor(serie: dict, chave: str, i: int):
    """Valor da posição `i` ou None: chave ausente ou null não viram 0 nos campos novos."""
    lista = serie.get(chave)
    return lista[i] if lista is not None and i < len(lista) else None


def parse_open_meteo(payload: dict,
                     inversao_vento_max_kmh: float = Limiares().inversao_vento_max_kmh) -> list[HoraPrevisao]:
    """Converte a resposta horária do Open-Meteo (primeiras 120 h); horários no fuso devolvido pela API.

    Vento e rajada saem a 2 m (`vento_2m`), a altura da barra de que a bula fala; a inversão usa esse vento.
    """
    fuso = ZoneInfo(payload["timezone"])
    h = payload["hourly"]
    horas = []
    for i, ts in enumerate(h["time"][:HORAS_PREVISAO]):
        antigos = {campo: _valor(h, chave, i) for campo, chave in CAMPOS.items()}
        antigos["vento_kmh"] = _a_2m(antigos["vento_kmh"])
        temp, umid, vento = antigos["temp_c"], antigos["umidade_pct"], antigos["vento_kmh"]
        altos = [t for t in (_valor(h, "temperature_80m", i), _valor(h, "temperature_120m", i)) if t is not None]
        dia = _valor(h, "is_day", i)
        horas.append(HoraPrevisao(
            ts=datetime.fromisoformat(ts).replace(tzinfo=fuso), **antigos,
            vento_dir_graus=_valor(h, "wind_direction_10m", i),
            rajada_kmh=_a_2m(_valor(h, "wind_gusts_10m", i)),
            delta_t_c=delta_t(temp, umid) if temp is not None and umid is not None else None,
            # sem temperatura a 2 m ou vento não há como julgar (um 0 inventaria inversão)
            inversao=None if temp is None or vento is None else inversao_provavel(
                temp, max(altos) if altos else None, vento, _valor(h, "cloud_cover", i),
                None if dia is None else bool(dia), inversao_vento_max_kmh)))
    return horas


def parse_diaria(payload: dict) -> list[DiaPrevisao]:
    """Converte o bloco `daily` do Open-Meteo; [] quando a resposta não traz previsão diária. Vento a 2 m."""
    d = payload.get("daily")
    if not d:
        return []
    return [
        DiaPrevisao(data=date.fromisoformat(dia), chuva_mm=_valor(d, "precipitation_sum", i) or 0.0,
                    prob_chuva_pct=_valor(d, "precipitation_probability_max", i),
                    temp_min_c=_valor(d, "temperature_2m_min", i), temp_max_c=_valor(d, "temperature_2m_max", i),
                    vento_max_kmh=_a_2m(_valor(d, "wind_speed_10m_max", i)),
                    rajada_max_kmh=_a_2m(_valor(d, "wind_gusts_10m_max", i)),
                    vento_dir_graus=_valor(d, "wind_direction_10m_dominant", i))
        for i, dia in enumerate(d["time"])
    ]


def parse_previsao(payload: dict,
                   inversao_vento_max_kmh: float = Limiares().inversao_vento_max_kmh) -> Previsao:
    """Único ponto que monta a `Previsao` a partir de uma resposta (ou cenário) no formato Open-Meteo."""
    return Previsao(horas=parse_open_meteo(payload, inversao_vento_max_kmh), dias=parse_diaria(payload))


class OpenMeteoFonte:
    nome = "open-meteo"

    def __init__(self, http: httpx.Client, api_key: str | None = None,
                 inversao_vento_max_kmh: float = Limiares().inversao_vento_max_kmh):
        self.http, self.api_key = http, api_key
        self.inversao_vento_max_kmh = inversao_vento_max_kmh

    def obter(self, lat: float, lon: float, agora: datetime) -> Previsao:
        params = parametros_open_meteo(lat, lon)
        url = OPEN_METEO
        if self.api_key:
            url, params["apikey"] = OPEN_METEO_CUSTOMER, self.api_key
        try:
            resposta = self.http.get(url, params=params, timeout=10)
            resposta.raise_for_status()
        except httpx.HTTPError as erro:
            # sem a URL (ela leva o `apikey`) e sem a causa encadeada, que também a mostraria
            if isinstance(erro, httpx.HTTPStatusError):
                motivo = f"Open-Meteo HTTP {erro.response.status_code}"
            else:
                motivo = f"Open-Meteo indisponível ({type(erro).__name__})"
            raise FontePrevisaoIndisponivel(motivo) from None
        return parse_previsao(resposta.json(), self.inversao_vento_max_kmh)


class ReplayFonte:
    """Previsão gravada (formato Open-Meteo), deslocada para começar na hora atual."""

    nome = "replay"

    def __init__(self, caminho, inversao_vento_max_kmh: float = Limiares().inversao_vento_max_kmh):
        self.horas = parse_previsao(json.loads(Path(caminho).read_text(encoding="utf-8")),
                                    inversao_vento_max_kmh).horas

    def obter(self, lat: float, lon: float, agora: datetime) -> Previsao:
        if not self.horas:
            return Previsao(horas=[])
        delta = agora.replace(minute=0, second=0, microsecond=0) - self.horas[0].ts
        return Previsao(horas=[h.model_copy(update={"ts": h.ts + delta}) for h in self.horas])
