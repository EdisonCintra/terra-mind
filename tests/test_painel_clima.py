"""Cartões de clima por talhão na Visão geral (E3): Delta T, inversão, vento, última leitura e 7 dias."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from painel import dados, main


@pytest.fixture
def cliente():
    return TestClient(main.app)


def horas(n=60, **sobre):
    base = datetime(2026, 11, 17, 15, 0, tzinfo=timezone.utc)  # 12h em São Paulo
    saida = []
    for i in range(n):
        h = {
            "ts": (base + timedelta(hours=i)).isoformat(),
            "chuva_mm": 0.0, "vento_kmh": 12.0, "umidade_pct": 60, "temp_c": 25.0,
            "vento_dir_graus": 225.0, "rajada_kmh": 25.0, "delta_t_c": 5.5, "inversao": False,
        }
        saida.append({**h, **sobre})
    return saida


def dias():
    return [
        {"data": f"2026-11-{18 + i}", "chuva_mm": 1.5 * i, "prob_chuva_pct": None if i == 0 else 40,
         "temp_min_c": 18.0, "temp_max_c": 30.0, "vento_max_kmh": 20.0, "rajada_max_kmh": None,
         "vento_dir_graus": 90.0}
        for i in range(7)
    ]


OBS_ESTACAO = {"ts": "2026-11-17T14:00:00+00:00", "chuva_mm": 0.0, "temp_c": 27.0, "umidade_pct": 55,
               "vento_kmh": 10.0, "vento_dir_graus": 90.0, "fonte": "estacao", "estacao": "A502",
               "distancia_km": 12.4}
OBS_REANALISE = {**OBS_ESTACAO, "fonte": "reanalise", "estacao": None, "distancia_km": None}


def monitor_falso(monkeypatch, previsao=None, diaria=None, observado=None):
    """Cada argumento: lista/dict (200), int (status de erro) ou Exception (conexão)."""
    respostas = {"/diaria": diaria, "/observado/": observado, "/previsao/": previsao}

    def responder(req):
        caminho = req.url.path
        chave = "/diaria" if caminho.endswith("/diaria") else "/observado/" if "/observado/" in caminho else "/previsao/"
        valor = respostas[chave]
        if isinstance(valor, Exception):
            raise valor
        if isinstance(valor, int):
            return httpx.Response(valor, json={"detail": "x"})
        return httpx.Response(200, json=valor)

    monkeypatch.setattr(main, "cliente_http", lambda: httpx.Client(transport=httpx.MockTransport(responder)))
    monkeypatch.setattr(dados, "talhao_padrao", lambda *_: 1)


def test_rumo():
    assert dados.rumo(0) == "N"
    assert dados.rumo(100) == "L"
    assert dados.rumo(350) == "N"
    assert dados.rumo(225) == "SO"
    assert dados.rumo(22.5) == "NE"
    assert dados.rumo(337.5) == "N"


def test_cartoes_com_dados(cliente, monkeypatch):
    h = horas()
    h[5]["inversao"] = True  # 5 h depois de 12h SP = 17h
    monitor_falso(monkeypatch, h, dias(), OBS_ESTACAO)
    corpo = cliente.get("/parcial/clima?talhao_id=1").text
    assert "Delta T" in corpo and "5,5" in corpo
    assert "Inversão" in corpo and "risco às 17/11 17h" in corpo
    assert "SO" in corpo and "25" in corpo
    assert corpo.count('class="dia"') == 7
    assert "18/11" in corpo and "—" in corpo
    assert "A502" in corpo and "12,4 km" in corpo
    assert 'class="vazio"' not in corpo


def test_delta_t_fora_da_faixa_e_atencao(cliente, monkeypatch):
    monitor_falso(monkeypatch, horas(delta_t_c=9.1), dias(), OBS_ESTACAO)
    corpo = cliente.get("/parcial/clima?talhao_id=1").text
    assert "9,1" in corpo and "atencao" in corpo


def test_inversao_sem_risco_e_sem_dado(cliente, monkeypatch):
    monitor_falso(monkeypatch, horas(), dias(), OBS_ESTACAO)
    assert "sem risco previsto" in cliente.get("/parcial/clima?talhao_id=1").text
    monitor_falso(monkeypatch, horas(inversao=None, delta_t_c=None), dias(), OBS_ESTACAO)
    corpo = cliente.get("/parcial/clima?talhao_id=1").text
    assert "sem dado" in corpo and "sem risco previsto" not in corpo


def test_cartoes_sem_previsao_mostram_vazio(cliente, monkeypatch):
    monitor_falso(monkeypatch, 404, 404, 404)
    r = cliente.get("/parcial/clima?talhao_id=1")
    assert r.status_code == 200
    assert 'class="vazio"' in r.text


def test_uma_chamada_falhando_nao_apaga_as_outras(cliente, monkeypatch):
    monitor_falso(monkeypatch, horas(), httpx.ConnectError("fora"), 503)
    r = cliente.get("/parcial/clima?talhao_id=1")
    assert r.status_code == 200
    assert "Delta T" in r.text and "SO" in r.text
    assert 'class="dia"' not in r.text and 'class="vazio"' in r.text


def test_reanalise_avisa_que_nao_e_estacao(cliente, monkeypatch):
    monitor_falso(monkeypatch, horas(), dias(), OBS_REANALISE)
    corpo = cliente.get("/parcial/clima?talhao_id=1").text
    assert "reanálise Open-Meteo — não é estação" in corpo
    assert "11:00" in corpo  # 14h UTC em São Paulo


def test_seletor_de_talhao(cliente, monkeypatch):
    """Um seletor só, na Visão geral (fora do parcial): manda nos cartões e no gráfico (A2)."""
    monitor_falso(monkeypatch, horas(), dias(), OBS_ESTACAO)
    monkeypatch.setattr(dados, "listar_talhoes", lambda *_: [{"id": 1, "nome": "Talhão 1"}, {"id": 2, "nome": "Talhão 2"}])
    tela = cliente.get("/visao-geral").text
    assert tela.count('name="talhao_id"') == 1 and '<option value="1" selected>Talhão 1</option>' in tela
    assert 'hx-get="/parcial/clima"' in tela and 'hx-include="#talhao-previsao"' in tela
    assert "change from:#talhao-previsao" in tela
    corpo = cliente.get("/parcial/clima?talhao_id=2").text
    assert "<select" not in corpo and "Delta T" in corpo


def test_previsao_com_corpo_de_outro_tipo_apaga_so_o_cartao(cliente, monkeypatch):
    monitor_falso(monkeypatch, {"x": 1}, dias(), OBS_ESTACAO)
    r = cliente.get("/parcial/clima?talhao_id=1")
    assert r.status_code == 200
    assert "Delta T" not in r.text and 'class="vazio"' in r.text
    assert r.text.count('class="dia"') == 7 and "A502" in r.text


def test_observado_como_lista_apaga_so_a_leitura(cliente, monkeypatch):
    monitor_falso(monkeypatch, horas(), dias(), [OBS_ESTACAO])
    r = cliente.get("/parcial/clima?talhao_id=1")
    assert r.status_code == 200
    assert "Última leitura" not in r.text and "Delta T" in r.text and r.text.count('class="dia"') == 7


def test_item_sem_ts_e_campos_nao_numericos_nao_dao_500(cliente, monkeypatch):
    h = horas()
    h[2]["inversao"] = True
    del h[2]["ts"]
    h[0]["delta_t_c"] = "abc"
    obs = {**OBS_ESTACAO, "temp_c": "quente"}
    monitor_falso(monkeypatch, h, [{"chuva_mm": 1}], {"fonte": "estacao"})
    r = cliente.get("/parcial/clima?talhao_id=1")
    assert r.status_code == 200 and "Vento" in r.text
    monitor_falso(monkeypatch, h, dias(), obs)
    r = cliente.get("/parcial/clima?talhao_id=1")
    assert r.status_code == 200 and "sem dado" in r.text


def test_vento_rotulado_com_a_altura_da_barra(cliente, monkeypatch):
    """I1: cartão, leitura e faixa diária dizem que o vento é a 2 m (o Monitor já converte a leitura)."""
    monitor_falso(monkeypatch, horas(), dias(), OBS_REANALISE)
    texto = cliente.get("/parcial/clima?talhao_id=1").text
    assert "Vento a 2 m</span>" in texto or "Vento a 2 m" in texto
    assert "vento a 2 m (altura da barra)" in texto and "Vento a 2 m (altura da barra), como na previsão" in texto
