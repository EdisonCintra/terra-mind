from datetime import date, timedelta

import httpx
import pytest
import respx

from monitor.fontes import (OPEN_METEO, OPEN_METEO_CUSTOMER, FontePrevisaoIndisponivel, OpenMeteoFonte,
                            chave_grade, parse_diaria, parse_open_meteo, parse_previsao)
from monitor.tests.conftest import AGORA


def test_parse_localiza_no_fuso_e_mantem_120_horas(payload_open_meteo):
    horas = parse_open_meteo(payload_open_meteo)
    assert len(horas) == 120
    assert horas[0].ts.utcoffset() == timedelta(hours=-3)
    assert horas[0].chuva_mm == payload_open_meteo["hourly"]["precipitation"][0]
    assert horas[1].ts - horas[0].ts == timedelta(hours=1)


def test_vento_vem_convertido_para_2_m(payload_open_meteo):
    payload_open_meteo["hourly"]["wind_speed_10m"][0] = 20.0
    assert parse_open_meteo(payload_open_meteo)[0].vento_kmh == 15.0  # 20 × 0,748 (FAO-56)


@pytest.mark.parametrize("chave,campo", [
    ("precipitation", "chuva_mm"), ("wind_speed_10m", "vento_kmh"),
    ("relative_humidity_2m", "umidade_pct"), ("temperature_2m", "temp_c"),
])
def test_parse_valor_nulo_vira_none_nunca_zero(payload_open_meteo, chave, campo):
    """Contrato 21: dado ausente é None, nunca 0 (umidade 0% inventaria um gatilho de umidade)."""
    payload_open_meteo["hourly"][chave][0] = None
    assert getattr(parse_open_meteo(payload_open_meteo)[0], campo) is None


def test_umidade_e_vento_nulos_nao_inventam_gatilho_nem_risco():
    from monitor.avaliacao import avaliar
    from monitor.modelos import Limiares
    from monitor.risco import classificar

    horas = parse_open_meteo(_payload_curto(time=["2026-10-30T08:00"], relative_humidity_2m=[None],
                                            wind_speed_10m=[None], precipitation=[None],
                                            temperature_80m=[30.0], cloud_cover=[0], is_day=[0]))
    hora = horas[0]
    assert hora.umidade_pct is None and hora.vento_kmh is None and hora.chuva_mm is None
    assert hora.delta_t_c is None and hora.inversao is None  # vento ausente: não dá para julgar a inversão
    assert avaliar(horas, Limiares(), AGORA) == []
    assert classificar(1, horas, Limiares(), AGORA).nivel == "verde"


@respx.mock
def test_fonte_pede_parametros_certos(payload_open_meteo):
    rota = respx.get(OPEN_METEO).respond(200, json=payload_open_meteo)
    with httpx.Client() as http:
        OpenMeteoFonte(http).obter(-17.79, -50.92, AGORA)
    params = rota.calls[0].request.url.params
    assert params["forecast_days"] == "16" and params["wind_speed_unit"] == "kmh"
    assert params["timezone"] == "America/Sao_Paulo"


@respx.mock
def test_obter_devolve_previsao_com_120_horas(payload_open_meteo):
    respx.get(OPEN_METEO).respond(200, json=payload_open_meteo)
    with httpx.Client() as http:
        previsao = OpenMeteoFonte(http).obter(-17.79, -50.92, AGORA)
    assert len(previsao.horas) == 120 and len(previsao.dias) == 16


@respx.mock
def test_api_key_usa_customer_api(payload_open_meteo):
    rota = respx.get(OPEN_METEO_CUSTOMER).respond(200, json=payload_open_meteo)
    with httpx.Client() as http:
        OpenMeteoFonte(http, api_key="x").obter(-17.79, -50.92, AGORA)
    assert rota.calls[0].request.url.params["apikey"] == "x"


@respx.mock
def test_sem_api_key_nao_manda_apikey(payload_open_meteo):
    rota = respx.get(OPEN_METEO).respond(200, json=payload_open_meteo)
    with httpx.Client() as http:
        OpenMeteoFonte(http).obter(-17.79, -50.92, AGORA)
    assert "apikey" not in rota.calls[0].request.url.params


def test_chave_grade_arredonda_em_duas_casas():
    assert chave_grade(-17.7901, -50.9201) == chave_grade(-17.7899, -50.9199) == (-17.79, -50.92)


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


@respx.mock
def test_erro_http_com_api_key_nao_vaza_a_chave():
    respx.get(OPEN_METEO_CUSTOMER).respond(401)
    with httpx.Client() as http, pytest.raises(FontePrevisaoIndisponivel) as exc:
        OpenMeteoFonte(http, api_key="SEGREDO").obter(-17.79, -50.92, AGORA)
    assert "SEGREDO" not in str(exc.value) and "401" in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


@respx.mock
def test_falha_de_conexao_com_api_key_nao_vaza_a_chave():
    respx.get(OPEN_METEO_CUSTOMER).mock(side_effect=httpx.ConnectError("falhou"))
    with httpx.Client() as http, pytest.raises(FontePrevisaoIndisponivel) as exc:
        OpenMeteoFonte(http, api_key="SEGREDO").obter(-17.79, -50.92, AGORA)
    assert "SEGREDO" not in str(exc.value) and exc.value.__cause__ is None


@respx.mock
def test_erros_do_ciclo_nao_contem_a_chave():
    from monitor.ciclo import executar_ciclo
    from monitor.modelos import Limiares
    from monitor.tests.test_ciclo import T1, NotificadorFalso, RepoFalso

    respx.get(OPEN_METEO_CUSTOMER).respond(401)
    with httpx.Client() as http:
        r = executar_ciclo(OpenMeteoFonte(http, api_key="SEGREDO"), RepoFalso([T1]), NotificadorFalso(),
                           Limiares(), AGORA)
    assert r.erros and "SEGREDO" not in " ".join(r.erros)


def _payload_curto(**hourly):
    """Payload mínimo de 1 hora; `hourly` sobrescreve/acrescenta chaves."""
    h = {"time": ["2026-10-30T00:00"], "precipitation": [0.0], "wind_speed_10m": [3.0],
         "relative_humidity_2m": [60.0], "temperature_2m": [25.0]}
    h.update(hourly)
    return {"timezone": "America/Sao_Paulo", "hourly": h}


def test_parse_campos_novos():
    p = _payload_curto(wind_speed_10m=[1.5], temperature_80m=[27.0], temperature_120m=[26.0], cloud_cover=[10], is_day=[0],
                       wind_direction_10m=[180.0], wind_gusts_10m=[8.5])
    hora = parse_open_meteo(p)[0]
    assert hora.inversao is True
    assert hora.delta_t_c is not None and hora.delta_t_c > 0
    assert hora.vento_dir_graus == 180.0 and hora.rajada_kmh == 6.4  # 8,5 a 10 m → 2 m (FAO-56)
    assert hora.vento_kmh == 1.1  # a inversão julga o vento a 2 m


def test_parse_sem_niveis_de_altura_nao_inventa():
    hora = parse_open_meteo(_payload_curto())[0]
    assert hora.inversao is None and hora.rajada_kmh is None
    assert hora.vento_dir_graus is None and hora.delta_t_c is not None


def test_parse_delta_t_none_sem_umidade_ou_temperatura():
    assert parse_open_meteo(_payload_curto(relative_humidity_2m=[None]))[0].delta_t_c is None
    assert parse_open_meteo(_payload_curto(temperature_2m=[None]))[0].delta_t_c is None


def test_horaria_cortada_em_120h(payload_open_meteo):
    h = payload_open_meteo["hourly"]
    assert len(h["time"]) > 120  # forecast_days=16 traz 384 horas
    assert len(parse_open_meteo(payload_open_meteo)) == 120


def test_parse_diaria_16_dias(payload_open_meteo):
    dias = parse_diaria(payload_open_meteo)
    d = payload_open_meteo["daily"]
    assert len(dias) == 16
    assert dias[0].data == date.fromisoformat(d["time"][0])
    assert dias[0].chuva_mm == d["precipitation_sum"][0]
    assert dias[0].temp_max_c == d["temperature_2m_max"][0]


def test_parse_diaria_ausente_ou_nulos():
    assert parse_diaria(_payload_curto()) == []
    p = _payload_curto()
    p["daily"] = {"time": ["2026-10-30"], "precipitation_sum": [1.0], "precipitation_probability_max": [None],
                  "temperature_2m_min": [18.0], "temperature_2m_max": [30.0], "wind_speed_10m_max": [9.0],
                  "wind_gusts_10m_max": [None], "wind_direction_10m_dominant": [None]}
    d = parse_diaria(p)[0]
    assert d.prob_chuva_pct is None and d.rajada_max_kmh is None and d.vento_dir_graus is None


def test_parse_previsao_junta_horas_e_dias(payload_open_meteo):
    prev = parse_previsao(payload_open_meteo)
    assert len(prev.horas) == 120 and len(prev.dias) == 16


def test_fonte_pede_campos_novos(payload_open_meteo):
    with respx.mock:
        rota = respx.get(OPEN_METEO).respond(200, json=payload_open_meteo)
        with httpx.Client() as http:
            previsao = OpenMeteoFonte(http).obter(-17.79, -50.92, AGORA)
    params = rota.calls[0].request.url.params
    for campo in ("wind_gusts_10m", "temperature_80m", "temperature_120m", "cloud_cover", "is_day"):
        assert campo in params["hourly"]
    assert "precipitation_probability_max" in params["daily"]
    assert len(previsao.dias) == 16


def test_inversao_none_sem_temperatura_a_2m():
    hora = parse_open_meteo(_payload_curto(temperature_2m=[None], wind_speed_10m=[1.0], temperature_80m=[25.0]))[0]
    assert hora.inversao is None and hora.temp_c is None


def test_diaria_com_nulos_nao_derruba_a_horaria(payload_open_meteo):
    d = payload_open_meteo["daily"]
    d["temperature_2m_min"][0] = d["temperature_2m_max"][0] = d["wind_speed_10m_max"][0] = None
    prev = parse_previsao(payload_open_meteo)
    assert len(prev.horas) == 120 and len(prev.dias) == 16
    assert prev.dias[0].temp_min_c is None and prev.dias[0].temp_max_c is None
    assert prev.dias[0].vento_max_kmh is None


@respx.mock
def test_limiar_de_inversao_do_ambiente_muda_o_resultado(monkeypatch):
    """M1: LIMIAR_INVERSAO_VENTO_MAX_KMH chega ao parse (vento de 3 km/h a 10 m = 2,2 km/h a 2 m)."""
    p = _payload_curto(wind_speed_10m=[3.0], temperature_80m=[27.0], temperature_120m=[26.0])  # 2 m: 2,2 km/h
    assert parse_previsao(p).horas[0].inversao is False  # padrão 2 km/h
    assert parse_previsao(p, inversao_vento_max_kmh=3.0).horas[0].inversao is True
    monkeypatch.setenv("LIMIAR_INVERSAO_VENTO_MAX_KMH", "3")
    from monitor.main import _fonte_live_padrao

    fonte = _fonte_live_padrao()
    assert fonte.inversao_vento_max_kmh == 3.0
    respx.get(OPEN_METEO).respond(200, json=p)
    assert fonte.obter(-17.79, -50.92, AGORA).horas[0].inversao is True
