from datetime import timedelta

import httpx
import pytest
import respx

from monitor.fontes import OPEN_METEO, FontePrevisaoIndisponivel, OpenMeteoFonte, parse_open_meteo
from monitor.tests.conftest import AGORA


def test_parse_localiza_no_fuso_e_mantem_120_horas(payload_open_meteo):
    horas = parse_open_meteo(payload_open_meteo)
    assert len(horas) == 120
    assert horas[0].ts.utcoffset() == timedelta(hours=-3)
    assert horas[0].chuva_mm == payload_open_meteo["hourly"]["precipitation"][0]
    assert horas[1].ts - horas[0].ts == timedelta(hours=1)


def test_parse_trata_valor_nulo_como_zero(payload_open_meteo):
    payload_open_meteo["hourly"]["precipitation"][0] = None
    assert parse_open_meteo(payload_open_meteo)[0].chuva_mm == 0


@respx.mock
def test_fonte_pede_parametros_certos(payload_open_meteo):
    rota = respx.get(OPEN_METEO).respond(200, json=payload_open_meteo)
    with httpx.Client() as http:
        OpenMeteoFonte(http).obter(-17.79, -50.92, AGORA)
    params = rota.calls[0].request.url.params
    assert params["forecast_days"] == "5" and params["wind_speed_unit"] == "kmh"
    assert params["timezone"] == "America/Sao_Paulo"


@respx.mock
def test_erro_http_vira_indisponivel():
    respx.get(OPEN_METEO).respond(500)
    with httpx.Client() as http, pytest.raises(FontePrevisaoIndisponivel):
        OpenMeteoFonte(http).obter(-17.79, -50.92, AGORA)


@respx.mock
def test_timeout_vira_indisponivel():
    respx.get(OPEN_METEO).mock(side_effect=httpx.ReadTimeout("lento"))
    with httpx.Client() as http, pytest.raises(FontePrevisaoIndisponivel):
        OpenMeteoFonte(http).obter(-17.79, -50.92, AGORA)
