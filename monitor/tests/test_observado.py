import json
from datetime import timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from monitor.main import CENARIOS, SP, criar_app
from monitor.modelos import Estacao, LeituraObservada
from monitor.observado import (OPEN_METEO_ARCHIVE, FonteObservadaIndisponivel, ReanaliseOpenMeteo,
                               coletar_observacoes, estacao_mais_proxima, parse_reanalise, ultima_com_dado)
from monitor.tests.conftest import AGORA, FIXTURES
from monitor.tests.test_ciclo import T1, FonteFalsa, NotificadorFalso, RepoFalso

ARQUIVO = json.loads((FIXTURES / "open_meteo_archive.json").read_text(encoding="utf-8"))
PERTO = Estacao(codigo="A1", nome="Perto", lat=-17.80, lon=-50.92, fonte="inmet")
LONGE = Estacao(codigo="A2", nome="Longe", lat=-17.40, lon=-50.50, fonte="inmet")


def test_estacao_mais_proxima():
    estacao, km = estacao_mais_proxima(-17.79, -50.92, [LONGE, PERTO])
    assert estacao.codigo == "A1" and 0.5 < km < 2


def test_estacao_fora_do_raio_none():
    assert estacao_mais_proxima(-17.79, -50.92, [LONGE], raio_km=10) is None
    assert estacao_mais_proxima(-17.79, -50.92, []) is None


def test_parse_reanalise_marca_fonte():
    leituras = parse_reanalise(ARQUIVO)
    assert len(leituras) == len(ARQUIVO["hourly"]["time"])
    primeira = leituras[0]
    assert primeira.fonte == "reanalise" and primeira.estacao is None and primeira.distancia_km is None
    assert primeira.ts.utcoffset() == timedelta(hours=-3)
    assert primeira.chuva_mm == ARQUIVO["hourly"]["precipitation"][0]


def test_parse_reanalise_null_vira_none():
    payload = {"timezone": "America/Sao_Paulo", "hourly": {
        "time": ["2026-11-05T00:00", "2026-11-05T01:00"], "precipitation": [1.2, None],
        "temperature_2m": [20.0, None], "relative_humidity_2m": [None, None]}}
    a, b = parse_reanalise(payload)
    assert a.chuva_mm == 1.2 and a.umidade_pct is None and a.vento_kmh is None
    assert b.chuva_mm is None and b.temp_c is None
    assert ultima_com_dado([a, b]) == a


@respx.mock
def test_reanalise_pede_campos_e_filtra_janela():
    rota = respx.get(OPEN_METEO_ARCHIVE).mock(return_value=httpx.Response(200, json=ARQUIVO))
    inicio = AGORA.replace(year=2026, month=11, day=5, hour=0)
    leituras = ReanaliseOpenMeteo(httpx.Client()).leituras(-17.79, -50.92, inicio, inicio + timedelta(hours=5))
    assert len(leituras) == 6
    params = rota.calls[0].request.url.params
    assert params["wind_speed_unit"] == "kmh" and params["timezone"] == "America/Sao_Paulo"
    assert "relative_humidity_2m" in params["hourly"] and params["start_date"] == "2026-11-05"


@respx.mock
def test_reanalise_erro_sem_url():
    respx.get(OPEN_METEO_ARCHIVE).mock(return_value=httpx.Response(500))
    with pytest.raises(FonteObservadaIndisponivel) as erro:
        ReanaliseOpenMeteo(httpx.Client()).leituras(-17.79, -50.92, AGORA, AGORA)
    assert "http" not in str(erro.value).lower().replace("open-meteo arquivo http", "")


class FonteObsFalsa:
    def __init__(self, leituras):
        self.leituras_, self.chamadas = leituras, 0

    def leituras(self, lat, lon, inicio, fim):
        self.chamadas += 1
        return self.leituras_


def cliente_obs(fonte, relogio=None):
    app = criar_app(repo=RepoFalso([T1]), notificador=NotificadorFalso(), fonte_live=FonteFalsa(),
                    ao_vivo=False, cenarios_dir=CENARIOS, fonte_obs=fonte, relogio=relogio or (lambda: AGORA))
    return TestClient(app)


def test_rota_observado_404():
    c = cliente_obs(FonteObsFalsa([]))
    assert c.get("/observado/1").status_code == 404
    assert c.get("/observado/1").json() == {"detail": "sem_observado"}
    assert c.get("/observado/99").json() == {"detail": "sem_observado"}


def test_rota_observado_ultima_hora_com_dado_e_cache():
    horas = parse_reanalise(ARQUIVO)
    vazia = horas[-1].model_copy(update={"chuva_mm": None, "temp_c": None, "umidade_pct": None,
                                         "vento_kmh": None, "vento_dir_graus": None,
                                         "ts": horas[-1].ts + timedelta(hours=1)})
    fonte = FonteObsFalsa([*horas, vazia])
    relogio = [AGORA]
    c = cliente_obs(fonte, lambda: relogio[0])
    corpo = c.get("/observado/1").json()
    assert corpo["fonte"] == "reanalise" and corpo["estacao"] is None and corpo["distancia_km"] is None
    assert corpo["ts"].startswith(horas[-1].ts.isoformat()[:13])
    c.get("/observado/1")
    assert fonte.chamadas == 1  # cache de 1 h
    relogio[0] = AGORA + timedelta(hours=1, minutes=1)
    c.get("/observado/1")
    assert fonte.chamadas == 2


def test_rota_observado_vento_sai_a_2_m_e_o_bruto_fica_na_leitura():
    from monitor.fontes import vento_2m

    horas = parse_reanalise(ARQUIVO)
    bruto = ultima_com_dado(horas).vento_kmh
    assert bruto is not None and bruto > 0
    corpo = cliente_obs(FonteObsFalsa(horas)).get("/observado/1").json()
    assert corpo["vento_kmh"] == vento_2m(bruto) and corpo["vento_kmh"] < bruto
    assert corpo["altura_vento_m"] == 2


def test_rota_observado_sem_vento_continua_none():
    horas = parse_reanalise(ARQUIVO)
    sem_vento = [h.model_copy(update={"vento_kmh": None}) for h in horas]
    assert cliente_obs(FonteObsFalsa(sem_vento)).get("/observado/1").json()["vento_kmh"] is None


def test_coleta_sem_estacao_nao_faz_nada():
    class Repo:
        def estacoes(self):
            return []

    fonte = FonteObsFalsa([])
    assert coletar_observacoes(Repo(), fonte, AGORA) == 0 and fonte.chamadas == 0
    assert coletar_observacoes(Repo(), None, AGORA) == 0


def leitura(h, chuva=1.0):
    return LeituraObservada(ts=AGORA.replace(hour=h), chuva_mm=chuva, fonte="estacao", estacao="A1")


def test_coleta_nao_duplica(engine_pg):
    from sqlalchemy import text

    from monitor.repositorio import RepositorioMonitor

    with engine_pg.begin() as conn:
        conn.execute(text("TRUNCATE clima.observacoes, clima.estacoes CASCADE"))
        conn.execute(text("INSERT INTO clima.estacoes VALUES ('A1', 'Perto', -17.80, -50.92, 'inmet')"))
    repo = RepositorioMonitor(engine_pg)
    assert repo.estacoes()[0].lat == pytest.approx(-17.80)
    assert repo.gravar_observacoes("A1", [leitura(7), leitura(8)]) == 2
    assert repo.gravar_observacoes("A1", [leitura(7), leitura(8)]) == 0
    fonte = FonteObsFalsa([leitura(7), leitura(9)])
    assert coletar_observacoes(repo, fonte, AGORA) == 1
    with engine_pg.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM clima.observacoes")).scalar_one() == 3
