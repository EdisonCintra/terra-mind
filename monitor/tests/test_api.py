import time
from pathlib import Path

from fastapi.testclient import TestClient

from monitor.main import criar_app
from monitor.tests.test_ciclo import T1, FonteFalsa, NotificadorFalso, RepoFalso

CENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


def cliente(modo="replay", **extra):
    app = criar_app(repo=RepoFalso([T1]), notificador=NotificadorFalso(), fonte_live=FonteFalsa(),
                    modo=modo, cenarios_dir=CENARIOS, **extra)
    return TestClient(app)


def test_health():
    assert cliente().get("/health").json() == {"status": "ok"}


def test_modo_replay_nao_inicia_loop():
    with cliente() as c:
        assert c.get("/status").json() == {"modo": "replay", "ultimo_ciclo_em": None,
                                           "ultimos_incidentes": [], "ultimos_erros": []}


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


def test_modo_live_roda_ciclo_em_background():
    with cliente(modo="live", intervalo_min=60) as c:
        for _ in range(40):
            if c.get("/status").json()["ultimo_ciclo_em"]:
                break
            time.sleep(0.05)
        status = c.get("/status").json()
    assert status["modo"] == "live" and status["ultimos_incidentes"] == [1]
