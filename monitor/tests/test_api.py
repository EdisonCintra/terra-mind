import time
from datetime import timedelta

from fastapi.testclient import TestClient

from monitor.main import CENARIOS, criar_app
from monitor.modelos import DiaPrevisao, HoraPrevisao, Previsao
from monitor.tests.conftest import AGORA
from monitor.tests.test_ciclo import T1, FonteFalsa, NotificadorFalso, RepoFalso


def cliente(ao_vivo=False, **extra):
    app = criar_app(repo=RepoFalso([T1]), notificador=NotificadorFalso(), fonte_live=FonteFalsa(),
                    ao_vivo=ao_vivo, cenarios_dir=CENARIOS, **extra)
    return TestClient(app)


def test_health():
    assert cliente().get("/health").json() == {"status": "ok"}


def test_testes_desligam_o_laco():
    with cliente() as c:
        assert c.get("/status").json() == {"ao_vivo": False, "ultimo_ciclo_em": None,
                                           "ultimos_incidentes": [], "ultimos_erros": []}


def test_sem_parametro_o_laco_roda():
    app = criar_app(repo=RepoFalso([T1]), notificador=NotificadorFalso(), fonte_live=FonteFalsa(),
                    cenarios_dir=CENARIOS, intervalo_min=60)
    with TestClient(app) as c:
        for _ in range(40):
            if c.get("/status").json()["ultimo_ciclo_em"]:
                break
            time.sleep(0.05)
        status = c.get("/status").json()
    assert status["ao_vivo"] is True and status["ultimo_ciclo_em"] is not None


def test_replay_abre_incidente_e_atualiza_status():
    with cliente() as c:
        r = c.post("/replay", json={"cenario": "chuva_80mm.json"})
        assert r.status_code == 200 and r.json() == {"incidentes": [1], "erros": []}
        assert c.get("/status").json()["ultimos_incidentes"] == [1]


def test_replay_rejeita_caminho():
    assert cliente().post("/replay", json={"cenario": "../.env"}).status_code == 422


def test_replay_cenario_inexistente():
    assert cliente().post("/replay", json={"cenario": "nao_existe.json"}).status_code == 404


def test_ciclo_manual_usa_fonte_live():
    assert cliente().post("/ciclo").json()["incidentes"] == [1]


def test_replay_marca_origem_replay():
    repo = RepoFalso([T1])
    app = criar_app(repo=repo, notificador=NotificadorFalso(), fonte_live=FonteFalsa(),
                    ao_vivo=False, cenarios_dir=CENARIOS)
    TestClient(app).post("/replay", json={"cenario": "chuva_80mm.json"})
    assert repo.origens == {1: "replay"}


def test_ciclo_manual_marca_origem_live():
    repo = RepoFalso([T1])
    app = criar_app(repo=repo, notificador=NotificadorFalso(), fonte_live=FonteFalsa(),
                    ao_vivo=False, cenarios_dir=CENARIOS)
    TestClient(app).post("/ciclo")
    assert repo.origens == {1: "live"}


def test_ao_vivo_roda_ciclo_em_background():
    with cliente(ao_vivo=True, intervalo_min=60) as c:
        for _ in range(40):
            if c.get("/status").json()["ultimo_ciclo_em"]:
                break
            time.sleep(0.05)
        status = c.get("/status").json()
    assert status["ao_vivo"] is True and status["ultimos_incidentes"] == [1]


def test_previsao_devolve_horas_futuras():
    repo = RepoFalso([T1])
    horas = [HoraPrevisao(ts=AGORA + timedelta(hours=i), chuva_mm=0.5, vento_kmh=5, umidade_pct=70, temp_c=25)
             for i in range(130)]
    repo.gravar_previsao(1, "open-meteo", Previsao(horas=horas))
    # relógio fixo, 5h30 depois da primeira hora: truncado na hora, as 5 primeiras ficam de fora
    relogio = lambda: AGORA + timedelta(hours=5, minutes=30)  # noqa: E731
    app = criar_app(repo=repo, notificador=NotificadorFalso(), fonte_live=FonteFalsa(), ao_vivo=False,
                    relogio=relogio)
    r = TestClient(app).get("/previsao/1")
    corpo = r.json()
    assert r.status_code == 200 and len(corpo) == 120
    assert corpo[0]["ts"].startswith("2026-10-30T13:00:00")
    assert r.headers["X-Previsao-Fonte"] == "open-meteo" and r.headers["X-Previsao-Buscada-Em"]


def test_previsao_sem_dado_404():
    r = cliente().get("/previsao/1")
    assert r.status_code == 404 and r.json() == {"detail": "sem_previsao"}


def test_previsao_diz_a_fonte():
    c = cliente()
    assert c.post("/replay", json={"cenario": "chuva_80mm.json"}).status_code == 200
    r = c.get("/previsao/1")
    assert r.status_code == 200 and r.headers["X-Previsao-Fonte"] == "replay"


def _app_com_dias(n=16):
    repo = RepoFalso([T1])
    dias = [DiaPrevisao(data=(AGORA + timedelta(days=i)).date(), chuva_mm=1.0, prob_chuva_pct=40,
                        temp_min_c=18, temp_max_c=30, vento_max_kmh=9, rajada_max_kmh=15, vento_dir_graus=90)
            for i in range(n)]
    repo.gravar_previsao(1, "open-meteo", Previsao(horas=[], dias=dias))
    return TestClient(criar_app(repo=repo, notificador=NotificadorFalso(), fonte_live=FonteFalsa(),
                                ao_vivo=False))


def test_rota_diaria_limita_dias():
    c = _app_com_dias()
    r = c.get("/previsao/1/diaria?dias=7")
    assert r.status_code == 200 and len(r.json()) == 7 and r.json()[0]["data"] == "2026-10-30"
    assert len(c.get("/previsao/1/diaria").json()) == 16
    assert c.get("/previsao/1/diaria?dias=17").status_code == 422
    assert c.get("/previsao/1/diaria?dias=0").status_code == 422


def test_rota_diaria_sem_previsao_404():
    r = cliente().get("/previsao/1/diaria")
    assert r.status_code == 404 and r.json() == {"detail": "sem_previsao"}


def test_rota_diaria_vazia_para_replay():
    c = cliente()
    c.post("/replay", json={"cenario": "chuva_80mm.json"})
    assert c.get("/previsao/1/diaria").json() == []


def _previsao_seca():
    return Previsao(horas=[HoraPrevisao(ts=AGORA + timedelta(hours=i), chuva_mm=0, vento_kmh=5,
                                        umidade_pct=70, temp_c=25) for i in range(48)])


def _cliente_risco(com_previsao=True):
    repo = RepoFalso([T1])
    if com_previsao:
        repo.gravar_previsao(T1.id, "open-meteo", _previsao_seca())
    app = criar_app(repo=repo, notificador=NotificadorFalso(), fonte_live=FonteFalsa(),
                    ao_vivo=False, cenarios_dir=CENARIOS, relogio=lambda: AGORA)
    return TestClient(app)


def test_rota_risco_lista_talhao_verde():
    r = _cliente_risco().get("/risco")
    assert r.status_code == 200
    assert r.json() == [{"talhao_id": T1.id, "nivel": "verde", "motivos": [], "detalhes": [], "ate": None}]


def test_rota_risco_ignora_talhao_sem_previsao():
    assert _cliente_risco(com_previsao=False).get("/risco").json() == []


def test_rota_risco_aceita_fazenda_id():
    # TODO contrato 20 (F2): com fazendas, filtra por fazenda; antes disso o parâmetro é ignorado.
    r = _cliente_risco().get("/risco", params={"fazenda_id": 1})
    assert r.status_code == 200 and len(r.json()) == 1


def test_rota_risco_omite_previsao_velha():
    repo = RepoFalso([T1])
    repo.gravar_previsao(T1.id, "open-meteo", _previsao_seca())
    app = criar_app(repo=repo, notificador=NotificadorFalso(), fonte_live=FonteFalsa(), ao_vivo=False,
                    cenarios_dir=CENARIOS, relogio=lambda: AGORA + timedelta(hours=48))
    assert TestClient(app).get("/risco").json() == []


def test_rota_risco_mantem_previsao_com_hora_na_janela():
    repo = RepoFalso([T1])
    repo.gravar_previsao(T1.id, "open-meteo", _previsao_seca())
    app = criar_app(repo=repo, notificador=NotificadorFalso(), fonte_live=FonteFalsa(), ao_vivo=False,
                    cenarios_dir=CENARIOS, relogio=lambda: AGORA + timedelta(hours=47))
    assert len(TestClient(app).get("/risco").json()) == 1
