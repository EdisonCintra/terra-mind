"""Telas 2 a 4 do painel: linha do tempo, aprovação e simular chuva (T9.3 a T9.5)."""

import json

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp_mcp import escrita
from painel import dados, main
from tests.conftest import DATABASE_URL


@pytest.fixture
def cliente():
    return TestClient(main.app)


def sql(consulta, params=()):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        cur = conn.execute(consulta, params)
        return cur.fetchone() if cur.description else None


def novo_incidente(status="aberto"):
    return sql(
        "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)"
        " VALUES (1, '[]', '[]', %s) RETURNING id",
        (status,),
    )[0]


def novo_plano(incidente_id, mensagem="Plano: remarcar a OS 1", escalado=False):
    return sql(
        "INSERT INTO agente.planos (incidente_id, opcoes_json, escalado_em)"
        " VALUES (%s, %s, CASE WHEN %s THEN now() END) RETURNING id",
        (incidente_id, json.dumps({"opcoes": [], "mensagem": mensagem}), escalado),
    )[0]


def http_falso(monkeypatch, handler):
    """Troca o cliente HTTP do painel por um com transporte simulado; devolve as requisições vistas."""
    vistas = []

    def responder(request):
        vistas.append(request)
        return handler(request)

    monkeypatch.setattr(main, "cliente_http", lambda: httpx.Client(transport=httpx.MockTransport(responder)))
    return vistas


# --- helpers puros -----------------------------------------------------------------------------
def test_separar_citacoes_tira_fonte_e_pagina_do_texto():
    texto, cits = dados.separar_citacoes(
        "Chuva em 4 h (fonte: bula-abacus-hc, p. 7); vento alto (fonte: bula-abacus-hc, p. 8)"
    )
    assert cits == [("bula-abacus-hc", "7"), ("bula-abacus-hc", "8")]
    assert "fonte" not in texto and texto.startswith("Chuva em 4 h")
    assert dados.separar_citacoes(None) == ("", [])


def test_resumo_mostra_so_o_que_mudou():
    antes = {"id": 1, "status": "agendada", "x": 1}
    depois = {"id": 1, "status": "suspensa_preventiva", "x": 1}
    assert dados.resumo(antes, depois) == ["status: agendada → suspensa_preventiva"]
    assert dados.resumo(None, {"plano_id": 3}) == ["plano_id: 3"]
    assert dados.resumo(None, None) == []


# --- tela 2: linha do tempo --------------------------------------------------------------------
def test_timeline_sem_incidentes(cliente):
    assert "Nenhum incidente" in cliente.get("/timeline").text


def test_timeline_mostra_citacao_e_mudanca_de_status(cliente):
    inc = novo_incidente()
    assert escrita.suspender_os(1, inc, "Chuva prevista antes das 4 h (fonte: bula-abacus-hc, p. 7)")["ok"]
    html = cliente.get(f"/timeline?incidente={inc}").text
    assert "bula-abacus-hc, p. 7" in html and 'class="citacao"' in html
    assert "Ordem suspensa" in html and "status: agendada" not in html  # só o título, sem o JSON cru
    assert "(fonte:" not in html  # a citação sai do texto e vira selo
    assert f"/parcial/timeline?incidente={inc}" in html
    assert "bula-abacus-hc, p. 7" in cliente.get(f"/parcial/timeline?incidente={inc}").text


def test_timeline_mostra_so_titulo_e_check_do_plano_validado(cliente):
    from json import dumps

    inc = novo_incidente()
    insere = "INSERT INTO agente.auditoria (incidente_id, no, acao, depois) VALUES (%s, %s, %s, %s::jsonb)"
    sql(insere, (inc, "validador", "validar_plano", dumps({"aprovado": True, "motivos": [], "volta": 1})))
    html = cliente.get(f"/parcial/timeline?incidente={inc}").text
    assert "Plano validado" in html and 'class="evento ok"' in html
    assert "motivos" not in html and "aprovado" not in html and "volta" not in html

    reprovado = dumps({"aprovado": False, "motivos": ["máquina ocupada"]})
    sql(insere, (inc, "validador", "validar_plano", reprovado))
    html = cliente.get(f"/parcial/timeline?incidente={inc}").text
    assert 'class="evento alerta"' in html and "máquina ocupada" in html


def test_cada_proposta_e_cada_plano_pendente_trazem_o_id_para_comparar_opcoes(cliente):
    from json import dumps

    inc = novo_incidente("aguardando_aprovacao")
    plano = novo_plano(inc, "Remarcar")
    opcao = {"os_id": 1, "novo_inicio": "2026-11-02T07:00:00-03:00"}
    proposta = dumps({"plano_id": plano, "opcoes": [opcao]})
    insere = "INSERT INTO agente.auditoria (incidente_id, no, acao, depois) VALUES (%s, %s, %s, %s::jsonb)"
    sql(insere, (inc, "supervisor", "propor_plano", proposta))
    html = cliente.get(f"/parcial/timeline?incidente={inc}").text
    assert "data-proposta" in html and "2026-11-02T07:00:00-03:00" in html and f'"plano": {plano}' in html
    assert f'data-plano="{plano}"' in cliente.get("/parcial/pendentes").text


def test_timeline_usa_o_incidente_mais_recente_por_padrao(cliente):
    insere = "INSERT INTO agente.auditoria (incidente_id, no, acao) VALUES (%s, %s, 'a')"
    sql(insere, (novo_incidente("encerrado"), "no_antigo"))
    sql(insere, (novo_incidente(), "no_novo"))
    html = cliente.get("/timeline").text
    assert "no_novo" in html and "no_antigo" not in html


# --- tela 3: aprovação -------------------------------------------------------------------------
def test_aprovacao_lista_so_planos_sem_resposta_com_selo_de_escalado(cliente):
    inc = novo_incidente("aguardando_aprovacao")
    pendente = novo_plano(inc, "Remarcar OS 1 para sexta", escalado=True)
    respondido = novo_plano(inc, "Plano antigo")
    sql("UPDATE agente.planos SET respondido_em = now(), aprovado_por = 'x' WHERE id = %s", (respondido,))
    html = cliente.get("/parcial/pendentes").text
    assert "Remarcar OS 1 para sexta" in html and "Plano antigo" not in html
    assert "escalado" in html
    for r in ("SIM", "NAO", "OPCOES"):
        assert f'hx-post="/aprovacao/{inc}/{pendente}/{r}"' in html


def test_aprovacao_vazia(cliente):
    assert "Nenhuma aprovação pendente" in cliente.get("/parcial/pendentes").text


def test_resposta_encaminha_ao_agents_com_o_formato_do_contrato(cliente, monkeypatch):
    vistas = http_falso(monkeypatch, lambda req: httpx.Response(200, json={"status": "registrada"}))
    monkeypatch.setenv("AGENTS_URL", "http://agents:8000")
    r = cliente.post("/aprovacao/7/3/SIM")
    assert "Resposta registrada: SIM" in r.text and 'class="mensagem-ok"' in r.text
    assert str(vistas[0].url) == "http://agents:8000/incidentes/7/resposta"
    assert json.loads(vistas[0].content) == {"plano_id": 3, "resposta": "SIM", "respondido_por": "painel"}


def test_409_vira_aviso_de_decisao_ja_registrada(cliente, monkeypatch):
    http_falso(monkeypatch, lambda req: httpx.Response(409, json={"detail": "resposta_repetida"}))
    r = cliente.post("/aprovacao/7/3/NAO")
    assert r.status_code == 200 and "Decisão já registrada" in r.text and "mensagem-aviso" in r.text


@pytest.mark.parametrize("status,trecho", [(404, "Plano não encontrado"), (500, "HTTP 500")])
def test_outros_erros_do_agents_viram_mensagem_de_erro(cliente, monkeypatch, status, trecho):
    http_falso(monkeypatch, lambda req: httpx.Response(status))
    r = cliente.post("/aprovacao/7/3/SIM")
    assert r.status_code == 200 and trecho in r.text and "mensagem-erro" in r.text


def test_agents_fora_do_ar_nao_quebra_a_pagina(cliente, monkeypatch):
    def cai(req):
        raise httpx.ConnectError("sem rede")

    http_falso(monkeypatch, cai)
    r = cliente.post("/aprovacao/7/3/SIM")
    assert r.status_code == 200 and "Não consegui falar" in r.text


def test_resposta_invalida_e_recusada(cliente):
    assert cliente.post("/aprovacao/7/3/TALVEZ").status_code == 422


def test_alerta_vermelho_para_decisao_manual(cliente):
    inc = novo_incidente("aguardando_decisao_manual")
    sql(
        "INSERT INTO agente.auditoria (incidente_id, no, acao, justificativa)"
        " VALUES (%s, 'painel', 'enviar_alerta', 'Sem evidência na bula')",
        (inc,),
    )
    html = cliente.get("/parcial/alertas").text
    assert "alerta-vermelho" in html and f"Incidente #{inc}" in html and "Sem evidência na bula" in html
    assert 'hx-get="/parcial/alertas"' in cliente.get("/os").text
    sql("UPDATE agente.incidentes SET status = 'encerrado' WHERE id = %s", (inc,))
    assert "alerta-vermelho" not in cliente.get("/parcial/alertas").text


# --- tela 4: simular chuva ---------------------------------------------------------------------
def test_simular_chuva_chama_o_replay_e_mostra_o_resultado(cliente, monkeypatch):
    corpo = {"incidentes": [{"id": 5, "talhao_id": 1}], "erros": ["talhão 2: sem previsão"]}
    vistas = http_falso(monkeypatch, lambda req: httpx.Response(200, json=corpo))
    monkeypatch.setenv("MONITOR_URL", "http://monitor:8020")
    html = cliente.post("/simular/chuva").text
    assert str(vistas[0].url) == "http://monitor:8020/replay"
    assert json.loads(vistas[0].content) == {"cenario": "chuva_80mm.json", "talhao_id": 1}
    assert "1 incidente(s), 1 erro(s)" in html and "id: 5" in html and "sem previsão" in html


@pytest.mark.parametrize("status,trecho", [(404, "não encontrado"), (422, "HTTP 422")])
def test_simular_chuva_trata_erros_do_monitor(cliente, monkeypatch, status, trecho):
    http_falso(monkeypatch, lambda req: httpx.Response(status))
    r = cliente.post("/simular/chuva")
    assert r.status_code == 200 and trecho in r.text and "mensagem-erro" in r.text


def test_simular_chuva_com_monitor_fora_do_ar(cliente, monkeypatch):
    def cai(req):
        raise httpx.ConnectError("sem rede")

    http_falso(monkeypatch, cai)
    assert "Não consegui falar com o Monitor" in cliente.post("/simular/chuva").text


def test_tela_simular_tem_o_botao(cliente):
    assert 'hx-post="/simular/chuva"' in cliente.get("/simular").text


def test_simular_chuva_usa_o_talhao_configurado(cliente, monkeypatch):
    vistas = http_falso(monkeypatch, lambda req: httpx.Response(200, json={"incidentes": [], "erros": []}))
    monkeypatch.setenv("TALHAO_DEMO", "2")
    cliente.post("/simular/chuva")
    assert json.loads(vistas[0].content)["talhao_id"] == 2


def test_tela_e_so_de_acompanhamento_sem_botoes_de_decisao(cliente):
    inc = novo_incidente("aguardando_aprovacao")
    novo_plano(inc, "Remarcar OS 1 para sexta")
    for rota in ("/os", "/timeline", "/aprovacao"):
        html = cliente.get(rota).text
        assert "Remarcar OS 1 para sexta" not in html and "hx-post=\"/aprovacao/" not in html
        assert "Decisão" not in html and 'id="status-incidente"' in html
        assert 'hx-post="/simular/chuva"' in html
