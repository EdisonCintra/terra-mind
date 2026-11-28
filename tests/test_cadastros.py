"""F1: cadastro pelo painel. Cada tela grava no banco, audita e respeita a fazenda; desativar não apaga.

Seed: fazenda 1 (Exemplo) tem os talhões 1 (polígono) e 2 (ponto), as máquinas A (1) e B (2), os operadores
João (1) e Carlos (2), os insumos Abacus HC (1) e Engeo Pleno S (2) e 3 OS agendadas (1, 2 e 3). A fazenda 2
(Demo) tem o talhão 3,
a máquina 3, o operador 3 e o Abacus HC (3), sem OS.
"""

import json
import math
from datetime import datetime, timedelta
from types import SimpleNamespace

import httpx
import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from erp_mcp import leitura
from monitor.repositorio import RepositorioMonitor
from painel import cadastros, dados, main
from painel.rotas_cadastros import exigir_gerente, tipo_do_insumo
from tests.conftest import DATABASE_URL
from tests.test_painel_telas import http_falso

AGORA = datetime.now(dados.FUSO).replace(minute=0, second=0, microsecond=0)
M_LAT = 111_194.9


def sql(consulta: str, *params):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        cur = conn.execute(consulta, params or None)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def cliente():
    c = TestClient(main.app)
    c.cookies.set("fazenda_id", "1")
    return c


def quadrado(lado_m: float, lat: float = -17.80, lon: float = -50.90) -> dict:
    dlat = lado_m / 2 / M_LAT
    dlon = lado_m / 2 / (M_LAT * math.cos(math.radians(lat)))
    anel = [
        [lon - dlon, lat - dlat],
        [lon + dlon, lat - dlat],
        [lon + dlon, lat + dlat],
        [lon - dlon, lat + dlat],
    ]
    return {"type": "Polygon", "coordinates": [anel]}


def talhao_form(nome="Talhão Novo", geom=None, **extra) -> dict:
    return {
        "nome": nome,
        "cultura": "soja",
        "area_ha": "",
        "data_plantio": "2026-10-01",
        "estagio_fenologico": "V3",
        "tipo_solo": "",
        "geom": json.dumps(geom or {"type": "Point", "coordinates": [-50.95, -17.85]}),
        **extra,
    }


def auditorias(acao: str) -> list:
    return sql(
        "SELECT no, acao, antes, depois, justificativa FROM agente.auditoria WHERE acao = %s ORDER BY id",
        acao,
    )


# --- talhão: ponto, polígono, contrato 18 ----------------------------------------------------------
def test_cria_talhao_ponto_com_area_digitada_audita_e_redireciona(cliente):
    r = cliente.post("/cadastros/talhoes", data=talhao_form(area_ha="42,5"), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/cadastros/talhoes?ok=criado"
    (linha,) = sql(
        "SELECT fazenda_id, nome, area_ha, lat, lon, geom, ativo FROM erp.talhoes WHERE nome = 'Talhão Novo'"
    )
    assert linha[0] == 1 and float(linha[2]) == 42.5 and linha[6] is True
    assert (float(linha[3]), float(linha[4])) == (-17.85, -50.95)  # o centro de um ponto é o próprio ponto
    assert linha[5] == {"type": "Point", "coordinates": [-50.95, -17.85]}
    (aud,) = auditorias("cadastro_talhoes_criar")
    assert aud[0] == "painel" and aud[2] is None and aud[3]["nome"] == "Talhão Novo"


def test_cria_talhao_poligono_e_a_area_e_o_centro_saem_do_desenho(cliente):
    geom = quadrado(1000)  # 1 km × 1 km = 100 ha
    cliente.post(
        "/cadastros/talhoes", data=talhao_form("Talhão Polígono", geom, area_ha=""), follow_redirects=False
    )
    (linha,) = sql("SELECT area_ha, lat, lon, geom->>'type' FROM erp.talhoes WHERE nome = 'Talhão Polígono'")
    assert float(linha[0]) == pytest.approx(100, rel=0.01) and linha[3] == "Polygon"
    assert float(linha[1]) == pytest.approx(-17.80, abs=1e-4) and float(linha[2]) == pytest.approx(
        -50.90, abs=1e-4
    )


def test_area_digitada_vence_a_do_poligono(cliente):
    cliente.post("/cadastros/talhoes", data=talhao_form("Talhão Área", quadrado(1000), area_ha="90"))
    assert float(sql("SELECT area_ha FROM erp.talhoes WHERE nome = 'Talhão Área'")[0][0]) == 90


@pytest.mark.parametrize(
    ("geom", "mensagem"),
    [
        ("", "Desenhe o talhão"),
        ("isto não é json", "Desenhe o talhão"),
        (json.dumps({"type": "Point", "coordinates": [500, 0]}), "fora do mundo"),
        (json.dumps({"type": "Polygon", "coordinates": [[[0, 0], [1, 1]]]}), "pelo menos 3 vértices"),
        (json.dumps({"type": "LineString", "coordinates": [[0, 0], [1, 1]]}), "um ponto ou um polígono"),
    ],
)
def test_talhao_sem_geometria_valida_e_recusado_sem_gravar(cliente, geom, mensagem):
    r = cliente.post("/cadastros/talhoes", data={**talhao_form(), "geom": geom})
    assert r.status_code == 422 and mensagem in r.text
    assert sql("SELECT count(*) FROM erp.talhoes")[0][0] == 3  # só o seed


def test_ponto_sem_area_pede_a_area(cliente):
    r = cliente.post("/cadastros/talhoes", data=talhao_form(area_ha=""))
    assert r.status_code == 422 and "um ponto não tem área" in r.text


def test_erros_de_campo_voltam_no_formulario_com_o_que_foi_digitado(cliente):
    r = cliente.post("/cadastros/talhoes", data=talhao_form("", area_ha="abc", data_plantio="31/12"))
    assert r.status_code == 422
    assert r.text.count("Obrigatório.") >= 1 and "Use um número." in r.text and "Data inválida." in r.text
    assert 'value="31/12"' in r.text  # o que o usuário digitou não se perde


def test_area_absurda_nao_vira_erro_500(cliente):
    r = cliente.post("/cadastros/talhoes", data=talhao_form(area_ha="99999999999999"))
    assert r.status_code == 422 and "banco recusou" in r.text


def test_edita_talhao_mantem_a_geometria_e_audita_antes_e_depois(cliente):
    geom = json.dumps(sql("SELECT geom FROM erp.talhoes WHERE id = 1")[0][0])
    form = talhao_form("Talhão 1 - Renomeado", area_ha="120", geom=None) | {"geom": geom}
    r = cliente.post("/cadastros/talhoes/1", data=form, follow_redirects=False)
    assert r.headers["location"] == "/cadastros/talhoes?ok=alterado"
    assert sql("SELECT nome FROM erp.talhoes WHERE id = 1")[0][0] == "Talhão 1 - Renomeado"
    (aud,) = auditorias("cadastro_talhoes_editar")
    assert aud[2]["nome"] == "Talhão 1 - Sede" and aud[3]["nome"] == "Talhão 1 - Renomeado"


def test_formulario_de_edicao_traz_a_geometria_para_o_mapa(cliente):
    html = cliente.get("/cadastros/talhoes?editar=1").text
    assert 'id="mapa-editor"' in html and "&#34;Polygon&#34;" in html and "/static/editor_talhao.js" in html
    assert (
        "/static/editor_talhao.js" not in cliente.get("/cadastros/talhoes").text
    )  # só com o formulário aberto


def test_nao_edita_talhao_de_outra_fazenda(cliente):
    r = cliente.post("/cadastros/talhoes/3", data=talhao_form("Invasão", area_ha="10"))
    assert r.status_code == 422 and "não encontrado" in r.text
    assert sql("SELECT nome FROM erp.talhoes WHERE id = 3")[0][0] == "Talhão Demo"
    assert cliente.get("/cadastros/talhoes?editar=3").status_code == 404


def test_sem_fazenda_escolhida_nao_cadastra():
    c = TestClient(main.app)  # sem o cookie: "todas as fazendas"
    r = c.post("/cadastros/maquinas", data={"nome": "X", "tipo": "pulverizador", "status": "disponivel"})
    assert r.status_code == 422 and "Escolha uma fazenda" in r.text
    assert sql("SELECT count(*) FROM erp.maquinas")[0][0] == 3


# --- o Monitor vigia o talhão novo no ciclo seguinte e não vigia o desativado ----------------------------
def repositorio_do_monitor() -> RepositorioMonitor:
    return RepositorioMonitor(create_engine(DATABASE_URL.replace("postgresql://", "postgresql+psycopg://")))


def test_monitor_ve_o_talhao_novo_e_para_de_ver_o_desativado(cliente):
    repo = repositorio_do_monitor()
    assert [t.id for t in repo.talhoes()] == [1, 2, 3]
    cliente.post("/cadastros/talhoes", data=talhao_form("Talhão do Monitor", area_ha="10"))
    novo = sql("SELECT id FROM erp.talhoes WHERE nome = 'Talhão do Monitor'")[0][0]
    vistos = repo.talhoes()  # o ciclo seguinte lê o banco de novo
    assert novo in [t.id for t in vistos]
    (t,) = [t for t in vistos if t.id == novo]
    assert (t.lat, t.lon) == (-17.85, -50.95)  # lat e lon são o centro (contrato 18)
    assert cliente.post(f"/cadastros/talhoes/{novo}/desativar", follow_redirects=False).status_code == 303
    assert novo not in [t.id for t in repo.talhoes()]
    assert novo not in [t.id for t in repo.talhoes(ids=[novo])]


# --- desativar não apaga, esconde de todo mundo e tem trava -----------------------------------------------
def test_desativar_talhao_com_os_agendada_e_recusado_e_depois_de_cancelar_passa(cliente):
    r = cliente.post("/cadastros/talhoes/2/desativar")
    assert r.status_code == 422 and "OS agendada" in r.text
    assert sql("SELECT ativo FROM erp.talhoes WHERE id = 2")[0][0] is True
    assert cliente.post("/cadastros/os/2/cancelar", follow_redirects=False).status_code == 303
    r = cliente.post("/cadastros/talhoes/2/desativar", follow_redirects=False)
    assert r.headers["location"] == "/cadastros/talhoes?ok=desativado"
    assert sql("SELECT ativo FROM erp.talhoes WHERE id = 2")[0][0] is False  # a linha continua lá
    assert 2 not in [t["id"] for t in dados.listar_talhoes(1)]
    assert 2 not in [t["id"] for t in leitura.listar_talhoes(1)]
    assert 2 not in [t["id"] for t in dados.talhoes_com_geometria(1)]
    assert dados.indicadores(1, AGORA)["talhoes"] == 1
    assert "Desativado" in cliente.get("/cadastros/talhoes").text  # a lista ainda mostra, como desativado
    cliente.post("/cadastros/talhoes/2/reativar")
    assert sql("SELECT ativo FROM erp.talhoes WHERE id = 2")[0][0] is True
    assert [
        a[1]
        for a in sql("SELECT no, acao FROM agente.auditoria WHERE acao LIKE 'cadastro_talhoes_%' ORDER BY id")
    ] == [
        "cadastro_talhoes_desativar",
        "cadastro_talhoes_reativar",
    ]


def test_desativar_talhao_com_incidente_aberto_e_recusado(cliente):
    sql("UPDATE erp.ordens_servico SET status = 'cancelada' WHERE talhao_id = 1")
    sql("INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot) VALUES (1, '[]', '[]')")
    r = cliente.post("/cadastros/talhoes/1/desativar")
    assert r.status_code == 422 and "incidente" in r.text and "aberto" in r.text


# --- máquinas e operadores -------------------------------------------------------------------------------
def test_maquina_cria_edita_e_a_em_manutencao_some_da_disponibilidade(cliente):
    cliente.post(
        "/cadastros/maquinas", data={"nome": "Pulverizador C", "tipo": "pulverizador", "status": "disponivel"}
    )
    (nova,) = sql("SELECT id, fazenda_id FROM erp.maquinas WHERE nome = 'Pulverizador C'")
    inicio = AGORA + timedelta(days=30)

    def livres() -> list[str]:
        maquinas = leitura.disponibilidade_maquinas(inicio, inicio + timedelta(hours=4), talhao_id=1)
        return [m["nome"] for m in maquinas]

    assert nova[1] == 1 and "Pulverizador C" in livres()
    cliente.post(
        f"/cadastros/maquinas/{nova[0]}",
        data={"nome": "Pulverizador C", "tipo": "pulverizador", "status": "manutencao"},
    )
    assert "Pulverizador C" not in livres()
    cliente.post(
        f"/cadastros/maquinas/{nova[0]}",
        data={"nome": "Pulverizador C", "tipo": "pulverizador", "status": "disponivel"},
    )
    cliente.post(f"/cadastros/maquinas/{nova[0]}/desativar")
    assert "Pulverizador C" not in livres()  # desativada não é oferecida
    assert [a[1] for a in auditorias("cadastro_maquinas_criar")] == ["cadastro_maquinas_criar"]


def test_maquina_so_aceita_tipo_e_situacao_da_lista(cliente):
    r = cliente.post("/cadastros/maquinas", data={"nome": "Z", "tipo": "foguete", "status": "voando"})
    assert r.status_code == 422 and r.text.count("Escolha uma das opções.") == 2


def test_desativar_maquina_que_esta_numa_os_e_recusado(cliente):
    r = cliente.post("/cadastros/maquinas/1/desativar")  # o Pulverizador A está nas OS 1 e 2
    assert r.status_code == 422 and "OS agendada" in r.text
    assert sql("SELECT ativo FROM erp.maquinas WHERE id = 1")[0][0] is True


def test_operador_com_contatos_do_telegram_e_do_whatsapp(cliente):
    cliente.post(
        "/cadastros/operadores",
        data={"nome": "Ana", "telegram_chat_id": "-100123", "whatsapp_telefone": "5564999998888"},
    )
    assert sql(
        "SELECT telegram_chat_id, whatsapp_telefone, fazenda_id FROM erp.operadores WHERE nome = 'Ana'"
    ) == [(-100123, 5564999998888, 1)]
    r = cliente.post("/cadastros/operadores", data={"nome": "Bia", "whatsapp_telefone": "+55 64 9999"})
    assert r.status_code == 422 and "Use só dígitos." in r.text
    cliente.post("/cadastros/operadores", data={"nome": "Caio"})  # os dois contatos são opcionais
    assert sql("SELECT telegram_chat_id FROM erp.operadores WHERE nome = 'Caio'") == [(None,)]


# --- insumos ---------------------------------------------------------------------------------------------
def insumo_form(produto="Produto Novo", **extra) -> dict:
    return {
        "produto": produto,
        "tipo": "fungicida",
        "unidade": "L",
        "preco_unitario": "99,90",
        "estoque": "250",
        "bula_id": "",
        **extra,
    }


def test_insumo_cria_com_preco_em_virgula_e_audita(cliente):
    cliente.post("/cadastros/insumos", data=insumo_form())
    (preco, estoque, bula, fazenda) = sql(
        "SELECT preco_unitario, estoque, bula_id, fazenda_id FROM erp.insumos WHERE produto = 'Produto Novo'"
    )[0]
    assert (float(preco), float(estoque), bula, fazenda) == (99.9, 250.0, None, 1)
    assert len(auditorias("cadastro_insumos_criar")) == 1


def test_insumo_repetido_na_mesma_fazenda_e_recusado_mas_vale_em_outra(cliente):
    r = cliente.post("/cadastros/insumos", data=insumo_form("Abacus HC"))
    assert r.status_code == 422 and "já está cadastrado nesta fazenda" in r.text
    outra = TestClient(main.app)
    outra.cookies.set("fazenda_id", "2")
    assert (
        outra.post(
            "/cadastros/insumos", data=insumo_form("Engeo Pleno S"), follow_redirects=False
        ).status_code
        == 303
    )


def test_insumo_valida_estoque_negativo_unidade_e_bula_que_nao_existe(cliente):
    r = cliente.post(
        "/cadastros/insumos", data=insumo_form(estoque="-1", unidade="mL", bula_id="bula-que-nao-existe")
    )
    assert r.status_code == 422 and "Mínimo 0." in r.text and "Escolha uma das opções." in r.text
    assert "não está na base de conhecimento" in r.text


def test_insumo_com_bula_da_base_aceita():
    sql(
        "INSERT INTO conhecimento.documentos (id, titulo, tipo_doc, produto)"
        " VALUES ('bula-teste-f1', 'Bula teste', 'bula', 'Produto Bula')"
        " ON CONFLICT (id) DO NOTHING"
    )
    c = TestClient(main.app)
    c.cookies.set("fazenda_id", "1")
    r = c.post(
        "/cadastros/insumos",
        data=insumo_form("Produto Bula", bula_id="bula-teste-f1"),
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert sql("SELECT bula_id FROM erp.insumos WHERE produto = 'Produto Bula'") == [("bula-teste-f1",)]


def test_desativar_insumo_numa_os_e_recusado_e_o_desativado_some_das_listas(cliente):
    assert cliente.post("/cadastros/insumos/2/desativar").status_code == 422  # Engeo está na OS 3
    cliente.post("/cadastros/os/3/cancelar")
    cliente.post("/cadastros/insumos/2/desativar")
    assert "Engeo Pleno S" not in [i["produto"] for i in dados.listar_insumos(1)]
    assert "Engeo Pleno S" not in dados.listar_produtos(1)
    assert "Engeo Pleno S" not in [i["produto"] for i in dados.indicadores(1, AGORA)["insumos"]]


# --- planejamento de safra -------------------------------------------------------------------------------
def safra_form(**extra) -> dict:
    return {
        "talhao_id": "1",
        "safra": "2027/28",
        "cultura": "soja",
        "plantio_previsto": "2027-09-20",
        "status": "planejado",
        "observacoes": "",
        **extra,
    }


def test_safra_cria_e_aparece_no_indicador_da_fazenda(cliente):
    sql("DELETE FROM erp.planejamento_safra WHERE talhao_id = 1")
    cliente.post(
        "/cadastros/safra", data=safra_form(plantio_previsto=str((AGORA + timedelta(days=60)).date()))
    )
    t1 = {t["nome"]: t for t in dados.indicadores(1, AGORA)["plantio"]}["Talhão 1 - Sede"]
    assert (t1["proxima_safra"], t1["proxima_cultura"], t1["dias_ate_plantar"]) == ("2027/28", "soja", 60)


def test_safra_recusa_formato_duplicado_e_talhao_de_outra_fazenda(cliente):
    assert "Use o formato 2027/28." in cliente.post("/cadastros/safra", data=safra_form(safra="27-28")).text
    assert (
        "já tem planejamento" in cliente.post("/cadastros/safra", data=safra_form(safra="2026/27")).text
    )  # seed
    r = cliente.post("/cadastros/safra", data=safra_form(talhao_id="3"))
    assert r.status_code == 422 and "talhão ativo desta fazenda" in r.text
    assert (
        "Escolha uma das opções." in cliente.post("/cadastros/safra", data=safra_form(talhao_id="abc")).text
    )


def test_safra_desativar_cancela_e_reativar_volta_a_planejado(cliente):
    (plano,) = sql("SELECT id FROM erp.planejamento_safra WHERE talhao_id = 1")[0]
    cliente.post(f"/cadastros/safra/{plano}/desativar")
    assert sql("SELECT status FROM erp.planejamento_safra WHERE id = %s", plano)[0][0] == "cancelado"
    cliente.post(f"/cadastros/safra/{plano}/reativar")
    assert sql("SELECT status FROM erp.planejamento_safra WHERE id = %s", plano)[0][0] == "planejado"
    outra = TestClient(main.app)
    outra.cookies.set("fazenda_id", "2")
    assert outra.post(f"/cadastros/safra/{plano}/desativar").status_code == 422  # é de um talhão da fazenda 1


# --- OS --------------------------------------------------------------------------------------------------
def os_form(**extra) -> dict:
    inicio = (AGORA + timedelta(days=12)).replace(hour=7)
    return {
        "talhao_id": "1", "insumo_id": "1", "dose_por_ha": "0,3", "inicio": f"{inicio:%Y-%m-%dT%H:%M}",
        "duracao_horas": "4", "maquina_id": "2", "operador_id": "2", **extra,
    }  # fmt: skip


def test_os_cria_pelo_formulario_com_auditoria_e_vai_para_a_lista(cliente):
    r = cliente.post("/cadastros/os", data=os_form(), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/cadastros/os?ok=criado"
    assert sql("SELECT status, maquina_id, operador_id, fazenda_id FROM erp.ordens_servico WHERE id = 4") == [
        ("agendada", 2, 2, 1)
    ]
    assert len(auditorias("criar_os")) == 1
    assert "OS criada." in cliente.get("/cadastros/os?ok=criado").text


def test_os_recusa_maquina_ocupada_talhao_de_outra_fazenda_e_dados_ruins(cliente):
    os1 = sql("SELECT inicio_previsto FROM erp.ordens_servico WHERE id = 1")[0][0].astimezone(dados.FUSO)
    ocupada = cliente.post("/cadastros/os", data=os_form(inicio=f"{os1:%Y-%m-%dT%H:%M}", maquina_id="1"))
    assert ocupada.status_code == 422 and "já está na OS 1" in ocupada.text
    outro = cliente.post("/cadastros/os", data=os_form(talhao_id="3", insumo_id="3", maquina_id="3"))
    assert outro.status_code == 422 and "talhão ativo desta fazenda" in outro.text
    assert "Confira a data" in cliente.post("/cadastros/os", data=os_form(inicio="amanhã")).text
    assert "Confira a data" in cliente.post("/cadastros/os", data=os_form(dose_por_ha="0")).text
    assert "Escolha o pulverizador" in cliente.post("/cadastros/os", data=os_form(maquina_id="")).text
    assert sql("SELECT count(*) FROM erp.ordens_servico")[0][0] == 3


def test_os_nao_usa_talhao_maquina_operador_nem_insumo_desativados(cliente):
    sql("UPDATE erp.maquinas SET ativo = false WHERE id = 2")
    assert "pulverizador disponível" in cliente.post("/cadastros/os", data=os_form()).text
    sql("UPDATE erp.maquinas SET ativo = true WHERE id = 2")
    sql("UPDATE erp.operadores SET ativo = false WHERE id = 2")
    assert "operador desta fazenda" in cliente.post("/cadastros/os", data=os_form()).text
    sql("UPDATE erp.operadores SET ativo = true WHERE id = 2")
    sql("UPDATE erp.insumos SET ativo = false WHERE id = 1")
    assert "desativado" in cliente.post("/cadastros/os", data=os_form(maquina_id="2")).text
    sql("UPDATE erp.insumos SET ativo = true WHERE id = 1")
    sql("UPDATE erp.talhoes SET ativo = false WHERE id = 1")
    assert "talhão ativo" in cliente.post("/cadastros/os", data=os_form(maquina_id="2")).text


def test_edita_os_a_propria_maquina_continua_livre_e_audita(cliente):
    os1 = sql("SELECT inicio_previsto, maquina_id FROM erp.ordens_servico WHERE id = 1")[0]
    inicio = os1[0].astimezone(dados.FUSO) + timedelta(
        hours=1
    )  # sobrepõe o horário dela mesma: só a OS 1 conta
    form = {
        "dose_por_ha": "0,25",
        "inicio": f"{inicio:%Y-%m-%dT%H:%M}",
        "duracao_horas": "4",
        "maquina_id": str(os1[1]),
        "operador_id": "",
    }
    r = cliente.post("/cadastros/os/1", data=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/cadastros/os?ok=alterado"
    assert sql("SELECT dose_por_ha, duracao_horas, operador_id FROM erp.ordens_servico WHERE id = 1") == [
        (pytest.approx(0.25), pytest.approx(4), None)
    ]
    (aud,) = auditorias("editar_os")
    assert float(aud[2]["dose_por_ha"]) == 0.3 and float(aud[3]["dose_por_ha"]) == 0.25


def test_edicao_nao_deixa_roubar_a_maquina_de_outra_os(cliente):
    os3 = sql("SELECT inicio_previsto FROM erp.ordens_servico WHERE id = 3")[0][0].astimezone(dados.FUSO)
    form = {
        "dose_por_ha": "0,3",
        "inicio": f"{os3:%Y-%m-%dT%H:%M}",
        "duracao_horas": "4",
        "maquina_id": "2",
        "operador_id": "",
    }
    r = cliente.post("/cadastros/os/1", data=form)  # a máquina B está na OS 3 nesse horário
    assert r.status_code == 422 and "já está na OS 3" in r.text


def _incidente_em_andamento_na_os(os_id: int) -> None:
    sql(
        "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)"
        " SELECT talhao_id, '[]'::jsonb, '[]'::jsonb, 'aguardando_aprovacao'"
        " FROM erp.ordens_servico WHERE id = %s",
        os_id,
    )


def test_so_edita_ou_cancela_os_agendada_ou_reagendada(cliente):
    sql("UPDATE erp.ordens_servico SET status = 'executada' WHERE id = 1")
    sql("UPDATE erp.ordens_servico SET status = 'suspensa_preventiva' WHERE id = 2")
    _incidente_em_andamento_na_os(2)  # o plano ainda vai decidir a OS 2
    form = {
        "dose_por_ha": "0,3",
        "inicio": f"{AGORA + timedelta(days=20):%Y-%m-%dT%H:%M}",
        "duracao_horas": "4",
        "maquina_id": "1",
        "operador_id": "",
    }
    assert "executada e não pode ser editada" in cliente.post("/cadastros/os/1", data=form).text
    assert "suspensa por um incidente" in cliente.post("/cadastros/os/2/cancelar").text
    assert "não pode ser cancelada" in cliente.post("/cadastros/os/1/cancelar").text
    assert sql("SELECT id, status FROM erp.ordens_servico ORDER BY id")[:2] == [
        (1, "executada"),
        (2, "suspensa_preventiva"),
    ]


def test_os_suspensa_sem_incidente_em_andamento_pode_ser_remarcada(cliente):
    """Opções esgotadas ou decisão manual: sem isto a OS ficaria presa em `suspensa_preventiva`."""
    sql("UPDATE erp.ordens_servico SET status = 'suspensa_preventiva' WHERE id = 2")
    assert "Remarcar" in cliente.get("/cadastros/os").text
    nova = AGORA + timedelta(days=21)
    form = {
        "dose_por_ha": "0,3",
        "inicio": f"{nova:%Y-%m-%dT%H:%M}",
        "duracao_horas": "4",
        "maquina_id": "1",
        "operador_id": "",
    }
    r = cliente.post("/cadastros/os/2", data=form, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert sql("SELECT status FROM erp.ordens_servico WHERE id = 2") == [("agendada",)]
    assert sql("SELECT acao FROM agente.auditoria WHERE acao = 'remarcar_os_suspensa'") == [
        ("remarcar_os_suspensa",)
    ]


def test_os_suspensa_sem_incidente_em_andamento_pode_ser_cancelada(cliente):
    sql("UPDATE erp.ordens_servico SET status = 'suspensa_preventiva' WHERE id = 2")
    r = cliente.post("/cadastros/os/2/cancelar", follow_redirects=False)
    assert r.headers["location"] == "/cadastros/os?ok=cancelada"
    assert sql("SELECT status FROM erp.ordens_servico WHERE id = 2") == [("cancelada",)]


def test_cancela_os_libera_a_maquina_e_audita(cliente):
    assert (
        cliente.post("/cadastros/os/3/cancelar", follow_redirects=False).headers["location"]
        == "/cadastros/os?ok=cancelada"
    )
    assert sql("SELECT status FROM erp.ordens_servico WHERE id = 3") == [("cancelada",)]
    os3 = sql("SELECT inicio_previsto FROM erp.ordens_servico WHERE id = 3")[0][0]
    livres = dados.maquinas_livres(os3, os3 + timedelta(hours=4), 1)
    assert 2 in [m["id"] for m in livres]
    assert len(auditorias("cancelar_os")) == 1


def test_os_de_outra_fazenda_nao_se_edita_nem_cancela(cliente):
    sql(
        "INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, inicio_previsto)"
        " VALUES (3, 'pulverizacao', now() + interval '2 days')"
    )
    assert cliente.post("/cadastros/os/4/cancelar").status_code == 404
    assert cliente.get("/cadastros/os?editar=4").status_code == 404


def test_conferencia_da_janela_ao_editar_ignora_a_propria_os():
    os1 = sql("SELECT inicio_previsto FROM erp.ordens_servico WHERE id = 1")[0][0]
    fim = os1 + timedelta(hours=2)
    assert 1 not in [m["id"] for m in dados.maquinas_livres(os1, fim, 1)]  # ocupada por ela mesma
    assert 1 in [m["id"] for m in dados.maquinas_livres(os1, fim, 1, ignorar_os_id=1)]


def test_tela_de_edicao_da_os_pre_seleciona_a_maquina_e_confere_a_janela(cliente):
    html = cliente.get("/cadastros/os?editar=1").text
    assert 'name="os_id" value="1"' in html and 'name="maquina_atual" value="1"' in html
    assert "/static/agenda.js" in html and 'id="janela"' in html


# --- busca de produto (E5) ------------------------------------------------------------------------------
AGROFIT = [
    {
        "registro_mapa": "9210",
        "produto": "Abacus HC",
        "titular": "Basf",
        "classe": "Fungicida",
        "culturas": ["milho"],
        "url_bula": None,
        "bula_id": "bula-abacus-hc",
    },
    {
        "registro_mapa": "1",
        "produto": "Misto",
        "titular": "X",
        "classe": "Inseticida/Acaricida",
        "culturas": [],
        "url_bula": None,
        "bula_id": None,
    },
    {
        "registro_mapa": "2",
        "produto": "Só Ácaro",
        "titular": "Y",
        "classe": "Acaricida",
        "culturas": [],
        "url_bula": None,
        "bula_id": None,
    },
]


def test_classe_do_agrofit_vira_tipo_de_insumo_ou_fica_de_fora():
    assert tipo_do_insumo("Fungicida") == "fungicida" and tipo_do_insumo("Herbicida") == "herbicida"
    assert tipo_do_insumo("Inseticida/Acaricida") == "inseticida"
    assert tipo_do_insumo("Acaricida") is None and tipo_do_insumo(None) is None


def test_busca_de_produto_repassa_ao_conhecimento_e_marca_o_tipo(cliente, monkeypatch):
    vistas = http_falso(monkeypatch, lambda r: httpx.Response(200, json=AGROFIT))
    r = cliente.get("/api/produtos/busca", params={"q": "abacus"})
    assert r.status_code == 200 and [p["tipo_insumo"] for p in r.json()] == ["fungicida", "inseticida", None]
    assert r.json()[0]["bula_id"] == "bula-abacus-hc"
    assert vistas[0].url.path == "/produtos/busca" and vistas[0].url.params["q"] == "abacus"


def test_busca_de_produto_trata_busca_curta_agrofit_fora_e_conhecimento_fora(cliente, monkeypatch):
    assert cliente.get("/api/produtos/busca", params={"q": "ab"}).status_code == 422
    http_falso(monkeypatch, lambda r: httpx.Response(503, json={"detail": "agrofit_nao_configurado"}))
    r = cliente.get("/api/produtos/busca", params={"q": "abacus"})
    assert r.status_code == 503 and r.json() == {"detail": "agrofit_nao_configurado"}

    def cai(request):
        raise httpx.ConnectError("fora")

    http_falso(monkeypatch, cai)
    r = cliente.get("/api/produtos/busca", params={"q": "abacus"})
    assert r.status_code == 502 and "fora do ar" in r.json()["detail"]


def test_envio_da_bula_vai_como_arquivo_ao_conhecimento_e_devolve_o_bula_id(cliente, monkeypatch):
    vistas = http_falso(
        monkeypatch,
        lambda r: httpx.Response(201, json={"bula_id": "bula-misto", "produto": "Misto", "trechos": 42}),
    )
    r = cliente.post(
        "/api/produtos/1/bula", files={"arquivo": ("bula.pdf", b"%PDF-1.4 conteudo", "application/pdf")}
    )
    assert r.status_code == 201 and r.json()["bula_id"] == "bula-misto"
    assert vistas[0].url.path == "/produtos/1/bula" and b"%PDF-1.4 conteudo" in vistas[0].content
    assert cliente.post(
        "/api/produtos/..%2Fx/bula", files={"arquivo": ("b.pdf", b"x", "application/pdf")}
    ).status_code in (404, 422)
    assert (
        cliente.post(
            "/api/produtos/a b/bula", files={"arquivo": ("b.pdf", b"x", "application/pdf")}
        ).status_code
        == 422
    )


# --- quem pode e as páginas ------------------------------------------------------------------------------
def test_so_o_gerente_cadastra():
    def req(papel):
        return SimpleNamespace(state=SimpleNamespace(usuario=None if papel is None else {"papel": papel}))

    exigir_gerente(req(None))  # antes do login (F3): aberto
    exigir_gerente(req("gerente"))
    with pytest.raises(HTTPException) as e:
        exigir_gerente(req("operador"))
    assert e.value.status_code == 403


def test_menu_cadastros_existe_e_abas_inexistentes_dao_404(cliente):
    html = cliente.get("/visao-geral").text
    assert (
        'href="/cadastros"' in html
        and 'aria-disabled="true"' not in html.split('href="/cadastros"')[1].split(">")[0]
    )
    assert cliente.get("/cadastros/coisa").status_code == 404
    assert cliente.post("/cadastros/coisa", data={}).status_code == 404


def test_cada_aba_lista_so_a_fazenda_escolhida_e_mostra_desativados(cliente):
    sql("UPDATE erp.maquinas SET ativo = false WHERE id = 2")
    html = cliente.get("/cadastros/maquinas").text
    assert "Pulverizador A" in html and "Pulverizador B" in html and "Desativado" in html
    assert "Pulverizador Demo" not in html
    assert "Reativar" in html


# --- o critério de pronto do F1: tudo pela tela, sem SQL --------------------------------------------------
def test_gerente_cadastra_do_zero_dois_talhoes_maquina_operador_e_os_sem_sql(cliente):
    repo = repositorio_do_monitor()
    assert (
        cliente.post(
            "/cadastros/talhoes", data=talhao_form("Talhão Ponto", area_ha="30"), follow_redirects=False
        ).status_code
        == 303
    )
    assert (
        cliente.post(
            "/cadastros/talhoes",
            data=talhao_form("Talhão Mapa", quadrado(500, -17.9, -50.8)),
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        cliente.post(
            "/cadastros/maquinas",
            data={"nome": "Pulverizador Novo", "tipo": "pulverizador", "status": "disponivel"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        cliente.post(
            "/cadastros/operadores",
            data={"nome": "Operador Novo", "whatsapp_telefone": "5564999990000"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    ids = {
        n: i for n, i in sql("SELECT nome, id FROM erp.talhoes WHERE nome IN ('Talhão Ponto', 'Talhão Mapa')")
    }
    maquina = sql("SELECT id FROM erp.maquinas WHERE nome = 'Pulverizador Novo'")[0][0]
    operador = sql("SELECT id FROM erp.operadores WHERE nome = 'Operador Novo'")[0][0]
    form = os_form(
        talhao_id=str(ids["Talhão Mapa"]),
        maquina_id=str(maquina),
        operador_id=str(operador),
        dose_por_ha="0,2",
    )
    assert cliente.post("/cadastros/os", data=form, follow_redirects=False).status_code == 303
    nova = sql(
        "SELECT talhao_id, maquina_id, operador_id, status FROM erp.ordens_servico ORDER BY id DESC LIMIT 1"
    )[0]
    assert nova == (ids["Talhão Mapa"], maquina, operador, "agendada")
    # o polígono de 500 m × 500 m (25 ha) deu a área e o centro; o Monitor vigia os dois talhões novos
    assert float(sql("SELECT area_ha FROM erp.talhoes WHERE nome = 'Talhão Mapa'")[0][0]) == pytest.approx(
        25, rel=0.02
    )
    assert {t.id for t in repo.talhoes()} >= {ids["Talhão Ponto"], ids["Talhão Mapa"]}
    assert (
        len(
            sql(
                "SELECT 1 FROM agente.auditoria"
                " WHERE no = 'painel' AND (acao LIKE 'cadastro_%' OR acao = 'criar_os')"
            )
        )
        == 5
    )


def test_validacao_do_cadastro_nao_depende_do_navegador():
    ent = cadastros.MAQUINAS
    resultado = cadastros.salvar(ent, {"nome": "", "tipo": "pulverizador", "status": "disponivel"}, 1)
    assert not resultado.ok and resultado.erros == {"nome": "Obrigatório."}
    resultado = cadastros.salvar(ent, {"nome": "x" * 500, "tipo": "pulverizador", "status": "disponivel"}, 1)
    assert resultado.erros == {"nome": "No máximo 120 caracteres."}
