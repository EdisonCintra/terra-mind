"""Painel web: tela de OS e parcial do HTMX (T9.1, T9.2)."""

import pytest
from fastapi.testclient import TestClient

from erp_mcp import escrita
from painel.main import app


@pytest.fixture
def cliente():
    return TestClient(app)


def test_health(cliente):
    assert cliente.get("/health").json() == {"ok": True}


def test_raiz_redireciona_para_a_tela_de_os(cliente):
    r = cliente.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/os"


def test_tela_os_atualiza_a_cada_2s_com_htmx_local(cliente):
    html = cliente.get("/os").text
    assert 'hx-get="/parcial/os"' in html and 'hx-trigger="every 2s"' in html
    assert 'src="/static/htmx.min.js"' in html and "://" not in html  # sem CDN
    assert cliente.get("/static/htmx.min.js").status_code == 200


def test_parcial_lista_as_tres_os_do_seed_como_agendadas(cliente):
    html = cliente.get("/parcial/os").text
    assert html.count("<tr>") == 3 and html.count('class="status agendada"') == 3
    assert "Abacus HC" in html and "Pulverizador A" in html and "Talhão 1 - Sede" in html


def test_os_suspensa_fica_amarela(cliente):
    from erp_mcp.db import conectar

    with conectar() as conn:
        inc = conn.execute(
            "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot)"
            " VALUES (1, '[]', '[]') RETURNING id"
        ).fetchone()["id"]
    assert escrita.suspender_os(1, inc, "teste")["ok"]
    html = cliente.get("/parcial/os").text
    assert html.count('class="status suspensa"') == 1 and html.count('class="status agendada"') == 2
