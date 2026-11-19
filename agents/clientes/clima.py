"""Previsão horária por coordenada, para a agenda (G2), a janela da OS (G3) e o relatório (G7).

O incidente continua usando o `previsao_snapshot` do Monitor. Fora do incidente não há snapshot, então
a previsão vem direto do Open-Meteo, a mesma fonte do Monitor, com os campos opcionais do contrato 21:
vento e rajada convertidos para 2 m (`vento_2m`), direção do vento, o Delta T, calculado aqui da
temperatura e da umidade (fórmula de Stull), e a inversão térmica: ar a 80 m mais quente que a 2 m
com vento quase parado (`inversao_termica`).
Quando o Monitor expuser a previsão por talhão (contrato 19, E2), basta outra implementação de
`ClimaCliente`.

Com `dias_passados`, o Open-Meteo devolve as horas já passadas do mesmo modelo. É dado de modelo, não
de estação: quem mostra precisa dizer isso (`Previsao.fonte`).
"""

import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from agents.modelos import HoraPrevisao

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
FONTE_OPEN_METEO = "Open-Meteo (modelo de previsão, não é estação meteorológica)"
FUSO = "America/Sao_Paulo"
CAMPOS = {
    "chuva_mm": "precipitation",
    "vento_kmh": "wind_speed_10m",
    "umidade_pct": "relative_humidity_2m",
    "temp_c": "temperature_2m",
    "vento_dir_graus": "wind_direction_10m",
    "rajada_kmh": "wind_gusts_10m",
}
TEMP_80M = "temperature_80m"  # só para a inversão térmica; não vira campo da `HoraPrevisao`
OBRIGATORIOS = ("chuva_mm", "vento_kmh", "umidade_pct", "temp_c")
MAX_DIAS = 16  # limite do Open-Meteo

# Mesmo cálculo de `monitor.fontes.vento_2m`.
# O Open-Meteo dá o vento a 10 m; a bula e o anemômetro do operador falam do vento na altura da
# barra, perto de 2 m, onde venta menos. Perfil logarítmico da FAO-56 (eq. 47):
#     u2 = u10 × 4,87 / ln(67,8 × 10 − 5,42) ≈ u10 × 0,748
FATOR_VENTO_2M = 4.87 / math.log(67.8 * 10 - 5.42)


def vento_2m(vento_10m: float) -> float:
    """Vento (ou rajada) medido a 10 m convertido para 2 m, em km/h, com uma casa decimal."""
    return round(vento_10m * FATOR_VENTO_2M, 1)


MAX_DIAS_PASSADOS = 92
VALIDADE_CACHE_S = 15 * 60


class ClimaIndisponivel(Exception):
    pass


@dataclass
class Previsao:
    horas: list[HoraPrevisao]
    fonte: str


class ClimaCliente(Protocol):
    async def previsao(self, lat: float, lon: float, dias: int = 7, dias_passados: int = 0) -> Previsao: ...


def delta_t(temp_c: float, umidade_pct: float) -> float:
    """Temperatura do ar menos a de bulbo úmido (Stull, 2011), em °C. Vale de 5% a 99% de umidade."""
    ur = min(max(umidade_pct, 5.0), 99.0)
    bulbo_umido = (
        temp_c * math.atan(0.151977 * math.sqrt(ur + 8.313659))
        + math.atan(temp_c + ur)
        - math.atan(ur - 1.676331)
        + 0.00391838 * ur**1.5 * math.atan(0.023101 * ur)
        - 4.686035
    )
    return round(max(0.0, temp_c - bulbo_umido), 1)


# Inversão que importa para a deriva: o ar sobe de temperatura com a altura E o vento quase parou (a
# bula do Abacus HC, p. 7: "a ausência de vento pode indicar situação de inversão térmica"). Com vento
# forte a camada se mistura e o risco é o vento, que já tem limite próprio. A margem de 0,5 °C descarta
# o ruído do modelo. Os dois cortes são estimativa até a tabela do E7.
INVERSAO_DIF_MIN_C = 0.5
INVERSAO_VENTO_MAX_KMH = 6.0


def inversao_termica(temp_2m: float, temp_80m: float | None, vento_kmh: float) -> bool | None:
    """Inversão provável: 80 m pelo menos `INVERSAO_DIF_MIN_C` mais quente que 2 m, com vento abaixo de
    `INVERSAO_VENTO_MAX_KMH`. `None` quando o modelo não traz a temperatura a 80 m."""
    if temp_80m is None:
        return None
    return temp_80m - temp_2m >= INVERSAO_DIF_MIN_C and vento_kmh < INVERSAO_VENTO_MAX_KMH


def interpretar(payload: dict) -> list[HoraPrevisao]:
    """Resposta horária do Open-Meteo → `HoraPrevisao`. Hora sem um dos quatro campos básicos é
    descartada: um zero no lugar do dado faria uma hora sem previsão parecer boa para aplicar."""
    fuso = ZoneInfo(payload.get("timezone") or FUSO)
    series = payload["hourly"]
    horas: list[HoraPrevisao] = []
    for i, ts in enumerate(series["time"]):
        valores = {campo: (series.get(chave) or [])[i : i + 1] for campo, chave in CAMPOS.items()}
        valores = {campo: (v[0] if v else None) for campo, v in valores.items()}
        if any(valores[campo] is None for campo in OBRIGATORIOS):
            continue
        # A inversão abaixo usa este vento já a 2 m: "ausência de vento" é na altura da barra.
        valores["vento_kmh"] = vento_2m(valores["vento_kmh"])
        if valores["rajada_kmh"] is not None:
            valores["rajada_kmh"] = vento_2m(valores["rajada_kmh"])
        temp_80m = (series.get(TEMP_80M) or [])[i : i + 1]
        horas.append(
            HoraPrevisao(
                ts=datetime.fromisoformat(ts).replace(tzinfo=fuso),
                delta_t_c=delta_t(valores["temp_c"], valores["umidade_pct"]),
                inversao=inversao_termica(
                    valores["temp_c"], temp_80m[0] if temp_80m else None, valores["vento_kmh"]
                ),
                **valores,
            )
        )
    return horas


class OpenMeteoClima:
    """Uma consulta por ponto da grade e por horizonte, guardada por 15 min: talhões vizinhos caem no
    mesmo ponto (duas casas decimais, cerca de 1 km) e dividem a consulta."""

    def __init__(self, url: str = OPEN_METEO, timeout_s: float = 10):
        self._http = httpx.AsyncClient(timeout=timeout_s)
        self._url = url
        self._cache: dict[tuple, tuple[float, Previsao]] = {}

    async def previsao(self, lat, lon, dias=7, dias_passados=0):
        dias = min(max(dias, 1), MAX_DIAS)
        dias_passados = min(max(dias_passados, 0), MAX_DIAS_PASSADOS)
        chave = (round(float(lat), 2), round(float(lon), 2), dias, dias_passados)
        guardada = self._cache.get(chave)
        if guardada and time.monotonic() - guardada[0] < VALIDADE_CACHE_S:
            return guardada[1]
        params = {
            "latitude": chave[0],
            "longitude": chave[1],
            "hourly": ",".join([*CAMPOS.values(), TEMP_80M]),
            "forecast_days": dias,
            "past_days": dias_passados,
            "timezone": FUSO,
            "wind_speed_unit": "kmh",
        }
        try:
            r = await self._http.get(self._url, params=params)
            r.raise_for_status()
            previsao = Previsao(horas=interpretar(r.json()), fonte=FONTE_OPEN_METEO)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise ClimaIndisponivel(f"{type(e).__name__}: {e}") from e
        self._cache[chave] = (time.monotonic(), previsao)
        return previsao

    async def fechar(self) -> None:
        await self._http.aclose()
