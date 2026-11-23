"""Tela de histórico (E6): suspensões ao vivo conferidas contra a chuva observada."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from erp_mcp.db import conectar
from painel import dados
from painel.main import app

BASE = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)  # 12:00 em SP


@pytest.fixture
def cliente():
    return TestClient(app)


def _inserir(os_id, classe, prevista, observada, dia=0, fonte="estacao", limite=20):
    with conectar() as conn:
        inc = conn.execute(
            "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)"
            " VALUES (1, '[]', '[]', 'encerrado') RETURNING id"
        ).fetchone()["id"]
        inicio = BASE + timedelta(days=dia)
        conn.execute(
            "INSERT INTO clima.conferencias (incidente_id, os_id, janela_inicio, janela_fim,"
            " chuva_prevista_mm, chuva_observada_mm, limite_mm, limite_fonte, fonte_observado, classe)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, 'bula-abacus-hc:p.7', %s, %s)",
            (inc, os_id, inicio, inicio + timedelta(hours=6), prevista, observada, limite, fonte, classe),
        )
    return inc


def _quatro():
    _inserir(1, "correta", 30, 28, dia=0)
    _inserir(2, "correta", 25, 21, dia=1)
    _inserir(3, "falso_alarme", 22, 2, dia=2)
    _inserir(1, "sem_dado", None, None, dia=3)


def test_resumo_conta_classes():
    _quatro()
    r = dados.resumo_historico(None)
    assert (r["total"], r["corretas"], r["falsos_alarmes"], r["sem_dado"]) == (4, 2, 1, 1)
    assert r["pct_corretas"] == 66.7
    assert r["erro_medio_mm"] == 8.7  # (2 + 4 + 20) / 3
    assert [i["classe"] for i in r["itens"]] == ["sem_dado", "falso_alarme", "correta", "correta"]


def test_resumo_vazio_nao_divide_por_zero():
    r = dados.resumo_historico(None)
    assert r["total"] == 0 and r["pct_corretas"] is None and r["erro_medio_mm"] is None
    assert r["itens"] == []


def test_resumo_so_sem_dado_nao_tem_percentual():
    _inserir(1, "sem_dado", None, None)
    r = dados.resumo_historico(None)
    assert r["total"] == 1 and r["pct_corretas"] is None and r["erro_medio_mm"] is None


def test_tela_mostra_numeros_e_itens(cliente):
    _quatro()
    html = cliente.get("/historico").text
    assert "66,7%" in html and "8,7 mm" in html
    assert "Correta" in html and "Falso alarme" in html and "Sem dado" in html
    assert "20 mm (bula-abacus-hc:p.7)" in html
    assert "10/11 12:00–10/11 18:00" in html
    assert "Só incidentes ao vivo; simulações ficam fora." in html
    assert "reanálise" not in html and "&lt;" not in html and "class=\"selo risco\"" in html


def test_tela_marca_reanalise(cliente):
    _inserir(1, "correta", 30, 28, fonte="reanalise")
    html = cliente.get("/historico").text
    assert "reanálise, não estação" in html
    assert 'class="faixa' in html


def test_tela_sem_conferencias_mostra_vazio(cliente):
    html = cliente.get("/historico").text
    assert r'class="vazio"' in html
    assert "—" in html and "NaN" not in html
