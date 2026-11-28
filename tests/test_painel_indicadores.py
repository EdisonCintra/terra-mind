"""F5: indicadores da fazenda. Os números do bloco têm de bater com o banco da fazenda escolhida.

Seed, fazenda 1 (Exemplo): talhões 1 (120 ha, milho, plantado há 30 dias) e 2 (85,5 ha, soja, há 45 dias);
OS 1 (+20 h, Abacus HC, talhão 1), OS 2 (+6 dias, Abacus HC, talhão 2) e OS 3 (+8 dias, Engeo Pleno S,
talhão 1), todas agendadas; Abacus HC com 500 L e dose 0,3 L/ha; Engeo com 300 L e dose 0,25 L/ha;
Pulverizadores A e B.
Fazenda 2 (Demo): talhão 3 (100 ha), 1 máquina, 1 operador, Abacus HC com 200 L e nenhuma OS.
"""

import re
from datetime import datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from painel import dados, main
from tests.conftest import DATABASE_URL

AGORA = datetime.now(dados.FUSO)


def sql(consulta: str, *params):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        cur = conn.execute(consulta, params)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def cliente():
    return TestClient(main.app)


def por_nome(linhas, chave="produto"):
    return {linha[chave]: linha for linha in linhas}


# --- números da fazenda 1, calculados à mão a partir do seed -------------------------------------
def test_fazenda_1_bate_com_o_seed():
    i = dados.indicadores(1, AGORA)
    assert i["talhoes"] == 2 and i["area_ha"] == pytest.approx(205.5)
    assert i["os_por_status"] == {
        "agendada": 3,
        "suspensa_preventiva": 0,
        "reagendada": 0,
        "executada": 0,
        "cancelada": 0,
    }
    assert [o["id"] for o in i["os_proximas"]] == [1, 2]  # a OS 3 é daqui a 8 dias: fora dos 7
    assert i["maquinas"] == {"total": 2, "manutencao": 0, "nomes": ""}
    insumos = por_nome(i["insumos"])
    abacus, engeo = insumos["Abacus HC"], insumos["Engeo Pleno S"]
    assert float(abacus["estoque"]) == 500 and float(abacus["comprometido"]) == pytest.approx(
        120 * 0.3 + 85.5 * 0.3
    )
    assert abacus["saldo"] == pytest.approx(438.35) and abacus["cobre"] and abacus["os_agendadas"] == 2
    assert float(engeo["comprometido"]) == pytest.approx(120 * 0.25) and engeo["saldo"] == pytest.approx(270)
    assert i["insumos_sem_cobertura"] == []


def test_fazenda_2_e_o_total_sao_separados():
    demo = dados.indicadores(2, AGORA)
    assert demo["talhoes"] == 1 and demo["area_ha"] == pytest.approx(100)
    assert sum(demo["os_por_status"].values()) == 0 and demo["os_proximas"] == []
    assert [(p["produto"], float(p["estoque"]), float(p["comprometido"])) for p in demo["insumos"]] == [
        ("Abacus HC", 200.0, 0.0)
    ]
    assert demo["maquinas"]["total"] == 1
    todas = dados.indicadores(None, AGORA)
    assert todas["talhoes"] == 3 and todas["area_ha"] == pytest.approx(305.5)
    assert len(todas["insumos"]) == 3 and todas["maquinas"]["total"] == 3
    assert {p["fazenda"] for p in todas["insumos"]} == {"Fazenda Exemplo", "Fazenda Demo"}


# --- cada número acompanha uma mudança no banco ----------------------------------------------------
def test_os_por_status_acompanha_o_banco():
    sql("UPDATE erp.ordens_servico SET status = 'suspensa_preventiva' WHERE id = 1")
    sql("UPDATE erp.ordens_servico SET status = 'reagendada' WHERE id = 2")
    sql("UPDATE erp.ordens_servico SET status = 'executada' WHERE id = 3")
    sql(
        "INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, inicio_previsto, status)"
        " VALUES (2, 'pulverizacao', now() + interval '1 day', 'cancelada')"
    )
    por_status = dados.indicadores(1, AGORA)["os_por_status"]
    esperado = dict(
        sql("SELECT status, count(*) FROM erp.ordens_servico WHERE fazenda_id = 1 GROUP BY status")
    )
    assert por_status == {
        "agendada": 0,
        "suspensa_preventiva": 1,
        "reagendada": 1,
        "executada": 1,
        "cancelada": 1,
    }
    assert {k: v for k, v in por_status.items() if v} == esperado


def test_os_dos_proximos_7_dias_so_conta_agendada_e_reagendada_dentro_da_janela():
    sql("UPDATE erp.ordens_servico SET status = 'suspensa_preventiva' WHERE id = 1")  # suspensa não conta
    sql("UPDATE erp.ordens_servico SET inicio_previsto = now() - interval '1 hour' WHERE id = 2")  # passada
    sql(
        "UPDATE erp.ordens_servico SET inicio_previsto = now() + interval '6 days',"
        " status = 'reagendada' WHERE id = 3"
    )
    assert [o["id"] for o in dados.indicadores(1, AGORA)["os_proximas"]] == [3]


def test_insumo_cujo_estoque_nao_cobre_as_os_agendadas():
    sql("UPDATE erp.insumos SET estoque = 40 WHERE produto = 'Abacus HC' AND fazenda_id = 1")
    i = dados.indicadores(1, AGORA)
    (falta,) = i["insumos_sem_cobertura"]
    assert falta["produto"] == "Abacus HC" and not falta["cobre"]
    assert falta["saldo"] == pytest.approx(40 - 61.65)
    # OS suspensa deixa de comprometer o estoque: com a OS 2 suspensa, os 36 L da OS 1 cabem nos 40 L
    sql("UPDATE erp.ordens_servico SET status = 'suspensa_preventiva' WHERE id = 2")
    assert dados.indicadores(1, AGORA)["insumos_sem_cobertura"] == []


def test_maquinas_em_manutencao_com_os_nomes():
    sql("UPDATE erp.maquinas SET status = 'manutencao' WHERE nome = 'Pulverizador B'")
    assert dados.indicadores(1, AGORA)["maquinas"] == {"total": 2, "manutencao": 1, "nomes": "Pulverizador B"}
    assert dados.indicadores(2, AGORA)["maquinas"]["manutencao"] == 0  # a manutenção é só da fazenda 1


def test_plantado_e_proximo_plantio_por_talhao():
    plantio = por_nome(dados.indicadores(1, AGORA)["plantio"], "nome")
    t1, t2 = plantio["Talhão 1 - Sede"], plantio["Talhão 2 - Baixada"]
    assert (t1["cultura"], t1["estagio_fenologico"], t1["dias_de_plantio"]) == ("milho", "V6", 30)
    assert (t1["proxima_cultura"], t1["proxima_safra"], t1["dias_ate_plantar"]) == ("soja", "2026/27", 150)
    assert (t2["cultura"], t2["dias_de_plantio"]) == ("soja", 45)
    assert (t2["proxima_cultura"], t2["dias_ate_plantar"]) == ("milho", 90)


def test_proximo_plantio_ignora_planejamento_passado_cancelado_e_pega_o_mais_proximo():
    sql(
        "INSERT INTO erp.planejamento_safra (talhao_id, safra, cultura, plantio_previsto, status) VALUES"
        " (1, '2025/26', 'milho', current_date - 10, 'planejado'),"  # já passou
        " (1, '2027/28', 'milho', current_date + 20, 'cancelado'),"  # cancelado
        " (1, '2028/29', 'milho', current_date + 40, 'planejado')"  # o mais próximo que vale
    )
    t1 = por_nome(dados.indicadores(1, AGORA)["plantio"], "nome")["Talhão 1 - Sede"]
    assert (t1["proxima_safra"], t1["proxima_cultura"], t1["dias_ate_plantar"]) == ("2028/29", "milho", 40)
    sql("DELETE FROM erp.planejamento_safra WHERE talhao_id = 1")
    t1 = por_nome(dados.indicadores(1, AGORA)["plantio"], "nome")["Talhão 1 - Sede"]
    assert t1["plantio_previsto"] is None and t1["proxima_safra"] is None


def test_planejamento_de_safra_tem_um_por_talhao_e_safra_e_status_valido():
    with pytest.raises(psycopg.errors.UniqueViolation):
        sql(
            "INSERT INTO erp.planejamento_safra (talhao_id, safra, cultura, plantio_previsto)"
            " VALUES (1, '2026/27', 'soja', now())"
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        sql(
            "INSERT INTO erp.planejamento_safra (talhao_id, safra, cultura, plantio_previsto, status)"
            " VALUES (1, '2099/00', 'soja', now(), 'talvez')"
        )


# --- a tela --------------------------------------------------------------------------------------
def valor(html: str, id_: str) -> str:
    return re.search(rf'id="{id_}"[^>]*>([^<]*)<', html).group(1).strip()


def test_tela_mostra_os_numeros_da_fazenda_escolhida(cliente):
    cliente.cookies.set("fazenda_id", "1")
    r = cliente.get("/fazenda")
    assert r.status_code == 200 and "Fazenda Exemplo" in r.text
    assert (valor(r.text, "ind-talhoes"), valor(r.text, "ind-proximas")) == ("2", "2")
    assert (valor(r.text, "ind-manutencao"), valor(r.text, "ind-sem-cobertura")) == ("0", "0")
    assert [
        valor(r.text, f"ind-os-{s}") for s in ("agendada", "suspensa_preventiva", "reagendada", "executada")
    ] == [
        "3",
        "0",
        "0",
        "0",
    ]
    assert "205,5 ha no total" in r.text and "Sobra 438,35 L" in r.text and "Sobra 270,00 L" in r.text
    assert "Talhão Demo" not in r.text and "Pulverizador Demo" not in r.text


def test_tela_sem_fazenda_escolhida_soma_tudo_e_mostra_a_coluna_da_fazenda(cliente):
    r = cliente.get("/fazenda")
    assert "Todas as fazendas" in r.text and valor(r.text, "ind-talhoes") == "3"
    assert "305,5 ha no total" in r.text and "Fazenda Demo" in r.text and "Talhão Demo" in r.text


def test_tela_avisa_quando_o_estoque_nao_cobre(cliente):
    sql("UPDATE erp.insumos SET estoque = 40 WHERE produto = 'Abacus HC' AND fazenda_id = 1")
    cliente.cookies.set("fazenda_id", "1")
    r = cliente.get("/fazenda")
    assert valor(r.text, "ind-sem-cobertura") == "1 produto" and "Faltam 21,65 L" in r.text


def test_menu_leva_a_tela_de_indicadores(cliente):
    assert 'href="/fazenda"' in cliente.get("/visao-geral").text


def test_numero_no_formato_brasileiro():
    assert dados.numero(1234.5) == "1.234,5" and dados.numero(205.5, 1) == "205,5"
    assert dados.numero(438.35, 2) == "438,35" and dados.numero(0, 0) == "0"


def test_dias_de_plantio_usa_a_data_dada():
    amanha = AGORA + timedelta(days=1)
    t1 = por_nome(dados.indicadores(1, amanha)["plantio"], "nome")["Talhão 1 - Sede"]
    assert t1["dias_de_plantio"] == 31
