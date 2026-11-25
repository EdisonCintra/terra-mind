"""F2 (contrato 20): o painel filtra tudo pela fazenda escolhida e não cruza dados entre fazendas."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from painel import dados, escrita, main
from tests.test_painel_telas import novo_incidente, sql

AGORA = datetime.now(dados.FUSO).replace(minute=0, second=0, microsecond=0)

# Seed: fazenda 1 (Exemplo) tem os talhões 1 e 2 e 3 OS; fazenda 2 (Demo) tem o talhão 3 ("Talhão Demo"),
# o Pulverizador Demo (máquina 3), o Operador Demo (operador 3) e o Abacus HC do estoque dela (insumo 3).


@pytest.fixture
def cliente():
    return TestClient(main.app)


@pytest.fixture
def os_da_demo():
    """Uma OS na Fazenda Demo, para as duas fazendas terem o que mostrar."""
    sql(
        "INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, insumo_id, dose_por_ha, inicio_previsto,"
        " duracao_horas, maquina_id, operador_id) VALUES (3, 'pulverizacao', 3, 0.3,"
        " now() + interval '2 days', 6, 3, 3)"
    )


def test_sem_escolher_mostra_todas_as_fazendas(cliente, os_da_demo):
    html = cliente.get("/parcial/os").text
    assert "Talhão 1 - Sede" in html and "Talhão Demo" in html


@pytest.mark.parametrize(
    ("fazenda", "aparece", "some"),
    [("1", "Talhão 1 - Sede", "Talhão Demo"), ("2", "Talhão Demo", "Talhão 1 - Sede")],
)
def test_fazenda_escolhida_filtra_as_ordens(cliente, os_da_demo, fazenda, aparece, some):
    cliente.cookies.set("fazenda_id", fazenda)
    html = cliente.get("/parcial/os").text
    assert aparece in html and some not in html


def test_escolher_a_fazenda_guarda_o_cookie_e_volta_para_a_tela(cliente):
    r = cliente.post("/fazenda/selecionar", data={"fazenda_id": 2, "voltar": "/perda"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/perda"
    assert "fazenda_id=2" in r.headers["set-cookie"]
    r = cliente.post("/fazenda/selecionar", data={"fazenda_id": 0}, follow_redirects=False)  # 0 = todas
    assert "fazenda_id=" in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]


def test_menu_lista_as_fazendas_e_marca_a_escolhida(cliente):
    cliente.cookies.set("fazenda_id", "2")
    html = cliente.get("/visao-geral").text
    assert 'id="fazenda_id"' in html and "Todas as fazendas" in html and "Fazenda Exemplo" in html
    assert 'value="2" selected>Fazenda Demo' in html


def test_pendentes_incidentes_e_alertas_so_da_fazenda(cliente):
    inc1 = novo_incidente("aguardando_decisao_manual", talhao_id=1)
    inc3 = novo_incidente("aguardando_decisao_manual", talhao_id=3)
    assert [i["id"] for i in dados.incidentes_decisao_manual(fazenda_id=1)] == [inc1]
    assert [i["id"] for i in dados.incidentes_decisao_manual(fazenda_id=2)] == [inc3]
    assert [i["id"] for i in dados.listar_incidentes(fazenda_id=2)] == [inc3]
    assert {i["id"] for i in dados.listar_incidentes()} == {inc1, inc3}


def test_insumos_talhoes_operadores_e_produtos_por_fazenda():
    assert [t["nome"] for t in dados.listar_talhoes(2)] == ["Talhão Demo"]
    assert [o["nome"] for o in dados.listar_operadores(2)] == ["Operador Demo"]
    assert {i["fazenda_id"] for i in dados.listar_insumos(1)} == {1}
    assert [i["produto"] for i in dados.listar_insumos(2)] == ["Abacus HC"]
    assert dados.listar_produtos(2) == ["Abacus HC"]
    assert dados.listar_produtos(1) == ["Abacus HC", "Engeo Pleno S"]
    livres = dados.maquinas_livres(AGORA + timedelta(days=30), AGORA + timedelta(days=31), 3)
    assert [m["nome"] for m in livres] == ["Pulverizador Demo"]


def test_criar_os_recusa_dados_de_outra_fazenda():
    inicio = AGORA + timedelta(days=5)
    # talhão da Demo (3) com máquina, insumo e operador da Exemplo
    r = escrita.criar_os(3, 1, 0.3, inicio, 4, 1, None)
    assert r["ok"] is False and "não é do estoque da fazenda" in r["erro"]
    r = escrita.criar_os(3, 3, 0.3, inicio, 4, 1, None)  # insumo certo, máquina da outra fazenda
    assert r["ok"] is False and "desta fazenda" in r["erro"]
    r = escrita.criar_os(3, 3, 0.3, inicio, 4, 3, 1)  # máquina certa, operador da outra fazenda
    assert r["ok"] is False and "operador desta fazenda" in r["erro"]
    assert sql("SELECT count(*) FROM erp.ordens_servico WHERE talhao_id = 3")[0] == 0
    ok = escrita.criar_os(3, 3, 0.3, inicio, 4, 3, 3)
    assert ok["ok"] is True and ok["os"]["fazenda_id"] == 2


def test_perda_so_da_fazenda_escolhida(os_da_demo):
    inc1, inc3 = novo_incidente("encerrado", talhao_id=1), novo_incidente("encerrado", talhao_id=3)
    for inc, os_id, perda in ((inc1, 1, 100.0), (inc3, 4, 700.0)):
        sql(
            "INSERT INTO agente.impactos (incidente_id, os_id, talhao_id, produto, condicao,"
            " valor_aplicacao_rs, fator, fonte, perda_rs, situacao)"
            " SELECT %s, os.id, os.talhao_id, 'Abacus HC', 'chuva', %s, 0.5, 'estimativa: x', %s, 'evitada'"
            " FROM erp.ordens_servico os WHERE os.id = %s",
            (inc, perda * 2, perda, os_id),
        )
    hoje = AGORA.date()
    ontem = hoje - timedelta(days=1)
    assert dados.perda_evitada(ontem, hoje, fazenda_id=1)["total_rs"] == 100.0
    assert dados.perda_evitada(ontem, hoje, fazenda_id=2)["total_rs"] == 700.0
    assert dados.perda_evitada(ontem, hoje)["total_rs"] == 800.0
