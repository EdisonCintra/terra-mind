"""Mapa de risco (E4): talhões coloridos pelo risco das próximas 48 h; o clique mostra o motivo e as OS."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from painel import dados, main


@pytest.fixture
def cliente():
    return TestClient(main.app)


def monitor_falso(monkeypatch, resposta):
    """`resposta`: lista (200), int (status de erro) ou Exception (conexão)."""
    chamadas = []

    def responder(req):
        chamadas.append(req)
        if isinstance(resposta, Exception):
            raise resposta
        if isinstance(resposta, int):
            return httpx.Response(resposta, json={"detail": "x"})
        return httpx.Response(200, json=resposta)

    monkeypatch.setattr(main, "cliente_http", lambda: httpx.Client(transport=httpx.MockTransport(responder)))
    return chamadas


RISCO = [
    {"talhao_id": 1, "nivel": "vermelho", "motivos": ["Chuva de 80 mm prevista nas próximas 48 h"], "ate": None},
    {"talhao_id": 2, "nivel": "verde", "motivos": [], "ate": None},
]


def ordem(produto="Abacus HC", status="agendada", inicio=None):
    inicio = inicio or datetime.now(timezone.utc) + timedelta(hours=20)
    return {"id": 1, "tipo_operacao": "pulverizacao", "inicio_previsto": inicio, "status": status, "produto": produto}


def test_mapa_usa_leaflet_local(cliente):
    html = cliente.get("/mapa").text
    assert "/static/vendor/leaflet/leaflet.js" in html
    assert "/static/vendor/leaflet/leaflet.css" in html
    assert "/static/mapa.js" in html


def test_mapa_sem_url_externa_no_html(cliente):
    assert "://" not in cliente.get("/mapa").text


def test_mapa_embute_talhoes_como_json(cliente, monkeypatch):
    monkeypatch.setattr(
        dados, "talhoes_com_geometria", lambda *_: [{"id": 7, "nome": "T </script>", "lat": -17.8, "lon": -50.9, "geom": None}]
    )
    html = cliente.get("/mapa").text
    assert '<script type="application/json" id="talhoes">' in html
    assert "</script>" not in html.split('id="talhoes">')[1].split("</script>")[0]  # tojson escapa
    assert "-17.8" in html


def test_menu_habilita_mapa(cliente):
    html = cliente.get("/mapa").text
    assert 'href="/mapa" aria-current="page"' in html
    assert 'href="/mapa" aria-disabled' not in html


def test_api_risco_repassa_o_monitor(cliente, monkeypatch):
    chamadas = monitor_falso(monkeypatch, RISCO)
    r = cliente.get("/api/risco?fazenda_id=3")
    assert r.status_code == 200 and r.json() == RISCO
    assert chamadas[0].url.path == "/risco" and chamadas[0].url.params["fazenda_id"] == "3"


@pytest.mark.parametrize("falha", [httpx.ConnectError("fora"), 500, 404])
def test_api_risco_monitor_fora_do_ar_e_503(cliente, monkeypatch, falha):
    monitor_falso(monkeypatch, falha)
    r = cliente.get("/api/risco")
    assert r.status_code == 503 and r.json() == {"detail": "monitor_indisponivel"}


def test_talhao_vermelho_mostra_os_de_abacus(cliente, monkeypatch):
    monitor_falso(monkeypatch, RISCO)
    monkeypatch.setattr(dados, "ordens_do_talhao", lambda t, de, ate: [ordem()])
    html = cliente.get("/parcial/mapa/talhao/1").text
    assert "Chuva de 80 mm prevista" in html
    assert "Abacus HC" in html
    assert "Agendada" in html


def test_talhao_sem_os_mostra_vazio(cliente, monkeypatch):
    monitor_falso(monkeypatch, RISCO)
    monkeypatch.setattr(dados, "ordens_do_talhao", lambda t, de, ate: [])
    html = cliente.get("/parcial/mapa/talhao/2").text
    assert 'class="vazio"' in html and "Nenhuma ordem" in html


def test_parcial_com_monitor_fora_avisa_risco_indisponivel(cliente, monkeypatch):
    monitor_falso(monkeypatch, httpx.ConnectError("fora"))
    monkeypatch.setattr(dados, "ordens_do_talhao", lambda t, de, ate: [ordem()])
    html = cliente.get("/parcial/mapa/talhao/1").text
    assert "risco indisponível" in html.lower() and "Abacus HC" in html


def test_parcial_usa_janela_de_48h(cliente, monkeypatch):
    monitor_falso(monkeypatch, RISCO)
    janelas = []
    monkeypatch.setattr(dados, "ordens_do_talhao", lambda t, de, ate: janelas.append((t, de, ate)) or [])
    cliente.get("/parcial/mapa/talhao/1")
    talhao, de, ate = janelas[0]
    assert talhao == 1 and ate - de == timedelta(hours=48)


def test_ordens_do_talhao_filtra_talhao_e_janela():
    agora = datetime.now(timezone.utc)
    todas = dados.ordens_do_talhao(1, agora - timedelta(days=3650), agora + timedelta(days=3650))
    assert todas and all({"produto", "inicio_previsto", "status"} <= set(o) for o in todas)
    assert dados.ordens_do_talhao(1, agora - timedelta(days=3650), agora - timedelta(days=3649)) == []


def test_talhoes_com_geometria_funciona_sem_coluna_geom():
    talhoes = dados.talhoes_com_geometria()
    assert talhoes and {"id", "nome", "lat", "lon", "geom"} <= set(talhoes[0])
    assert all(isinstance(t["lat"], float) and isinstance(t["lon"], float) for t in talhoes)


def test_mapa_js_nao_injeta_nome_como_html(cliente):
    # Guarda estática (não há executor de JS nos testes): o Leaflet põe texto de tooltip em innerHTML,
    # então o nome do talhão tem de entrar por um nó com textContent.
    js = cliente.get("/static/mapa.js").text
    assert "bindTooltip(String(" not in js
    assert "rotulo.textContent" in js and "bindTooltip(rotulo)" in js
    assert "pointToLayer" in js  # ponto GeoJSON mantém a cor de risco


def _risco_estruturado(nivel="vermelho"):
    return [{"talhao_id": 5, "nivel": nivel, "ate": "2026-11-23T18:00:00-03:00",
             "motivos": ["rajada 22,9 km/h até 23/11 18h; limite 15 km/h", "inversão térmica prevista até 23/11 18h"],
             "detalhes": [
                 {"parametro": "rajada", "rotulo": "Rajada", "nivel": "vermelho", "valor": 22.9, "limite": 15.0,
                  "unidade": "km/h", "sentido": "max", "inicio": "2026-11-23T14:00:00-03:00",
                  "fim": "2026-11-23T18:00:00-03:00"},
                 {"parametro": "inversao", "rotulo": "Inversão térmica", "nivel": "vermelho", "sentido": "evento",
                  "inicio": "2026-11-23T02:00:00-03:00", "fim": "2026-11-23T05:00:00-03:00"}]}]


def test_cartao_mostra_um_motivo_por_linha_com_valor_limite_e_periodo(cliente, monkeypatch):
    monitor_falso(monkeypatch, _risco_estruturado())
    monkeypatch.setattr(dados, "ordens_do_talhao", lambda *a: [])
    html = cliente.get("/parcial/mapa/talhao/5").text
    assert html.count('class="risco-linha tom-risco"') == 2
    assert "Rajada" in html and "22,9 km/h" in html and "pico · limite 15 km/h" in html
    assert "14h–18h" in html  # mesmo dia: período curto
    assert "Inversão térmica" in html and "provável" in html
    assert "22.9" not in html  # nada de ponto decimal na tela
    assert "Evite agendar aplicação neste talhão até" in html


def test_cartao_sem_detalhes_cai_no_texto_dos_motivos(cliente, monkeypatch):
    monitor_falso(monkeypatch, RISCO)
    monkeypatch.setattr(dados, "ordens_do_talhao", lambda *a: [])
    html = cliente.get("/parcial/mapa/talhao/1").text
    assert '<ul class="motivos">' in html and "Chuva de 80 mm" in html and "risco-linha" not in html
