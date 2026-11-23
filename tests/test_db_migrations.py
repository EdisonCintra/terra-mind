"""Validação automatizada das migrations e do seed (T6.1 a T6.6)."""

import psycopg
import pytest
from psycopg.errors import CheckViolation, UniqueViolation
from psycopg.rows import dict_row

from tests.conftest import DATABASE_URL, SEED


@pytest.fixture
def conn():
    with psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False) as c:
        yield c
        c.rollback()


def test_seed_ordens_sem_sobreposicao_e_maquina_livre(conn):
    """T6.3, T6.4 e T6.5: 3 OS agendadas, 1 na janela de 24 h, estoque suficiente e zero sobreposição."""
    ordens = conn.execute(
        """
        SELECT os.*, t.area_ha, i.produto, i.tipo AS tipo_insumo, i.estoque, i.unidade
        FROM erp.ordens_servico os
        JOIN erp.talhoes t ON t.id = os.talhao_id
        JOIN erp.insumos i ON i.id = os.insumo_id
        ORDER BY os.id
        """
    ).fetchall()
    assert len(ordens) == 3
    assert all(o["status"] == "agendada" for o in ordens)
    assert all(o["tipo_insumo"] in ("fungicida", "herbicida", "inseticida") for o in ordens)
    assert all(o["estoque"] >= o["area_ha"] * o["dose_por_ha"] for o in ordens)

    na_janela_24h = conn.execute(
        """
        SELECT id FROM erp.ordens_servico
        WHERE inicio_previsto BETWEEN now() AND now() + interval '24 hours'
        """
    ).fetchall()
    assert [r["id"] for r in na_janela_24h] == [1]

    sobreposicoes = conn.execute(
        """
        SELECT a.id AS os_a, b.id AS os_b
        FROM erp.ordens_servico a
        JOIN erp.ordens_servico b
          ON a.id < b.id
         AND a.maquina_id = b.maquina_id
         AND a.status IN ('agendada', 'reagendada')
         AND b.status IN ('agendada', 'reagendada')
         AND a.inicio_previsto < b.inicio_previsto + (b.duracao_horas * interval '1 hour')
         AND b.inicio_previsto < a.inicio_previsto + (a.duracao_horas * interval '1 hour')
        """
    ).fetchall()
    assert sobreposicoes == []

    livres_remarcacao = conn.execute(
        """
        SELECT m.id, m.nome
        FROM erp.maquinas m
        WHERE m.status = 'disponivel'
          AND m.tipo = 'pulverizador'
          AND NOT EXISTS (
              SELECT 1 FROM erp.ordens_servico os
              WHERE os.maquina_id = m.id
                AND os.status IN ('agendada', 'reagendada')
                AND os.inicio_previsto < (now() + interval '120 hours')
                AND (now() + interval '36 hours')
                    < os.inicio_previsto + (os.duracao_horas * interval '1 hour')
          )
        ORDER BY m.id
        """
    ).fetchall()
    assert any(m["nome"] == "Pulverizador B" for m in livres_remarcacao)

    leituras = conn.execute("SELECT count(*) AS n FROM erp.leituras_solo").fetchone()
    assert leituras["n"] == 96


def test_checks_erp_recusam_fertilizante_adubacao_e_status_invalido(conn):
    """T6.1: CHECKs de escopo D2 (sem fertilizante/adubação) e de status."""
    with pytest.raises(CheckViolation):
        with conn.transaction():
            conn.execute(
                "INSERT INTO erp.insumos (produto, tipo, unidade, preco_unitario, estoque)"
                " VALUES ('Ureia', 'fertilizante', 'kg', 3.50, 1000)"
            )

    with pytest.raises(CheckViolation):
        with conn.transaction():
            conn.execute(
                "INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, inicio_previsto)"
                " VALUES (1, 'adubacao', now() + interval '10 hours')"
            )

    with pytest.raises(CheckViolation):
        with conn.transaction():
            conn.execute("UPDATE erp.ordens_servico SET status = 'suspensa' WHERE id = 1")


def test_indice_unico_parcial_incidente_por_talhao(conn):
    """T6.2 e T6.6: dois incidentes abertos no mesmo talhão falham; encerrando o primeiro, o segundo passa."""
    inc1 = conn.execute(
        """
        INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)
        VALUES (1, '[{"tipo":"chuva"}]'::jsonb, '[]'::jsonb, 'aberto')
        RETURNING id
        """
    ).fetchone()["id"]

    with pytest.raises(UniqueViolation):
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)
                VALUES (1, '[{"tipo":"chuva"}]'::jsonb, '[]'::jsonb, 'aguardando_aprovacao')
                """
            )

    # Outro talhão pode ter incidente aberto ao mesmo tempo.
    conn.execute(
        """
        INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)
        VALUES (2, '[{"tipo":"chuva"}]'::jsonb, '[]'::jsonb, 'aberto')
        """
    )

    # Com o primeiro encerrado, abrir novo incidente no mesmo talhão passa.
    conn.execute("UPDATE agente.incidentes SET status = 'encerrado' WHERE id = %s", (inc1,))
    inc2 = conn.execute(
        """
        INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)
        VALUES (1, '[{"tipo":"chuva"}]'::jsonb, '[]'::jsonb, 'aberto')
        RETURNING id
        """
    ).fetchone()["id"]
    assert inc2 > inc1

    # Confere colunas novas de agente.planos (criado_em e escalado_em).
    plano = conn.execute(
        """
        INSERT INTO agente.planos (incidente_id, opcoes_json)
        VALUES (%s, '{"opcoes":[]}'::jsonb)
        RETURNING id, criado_em, escalado_em
        """,
        (inc2,),
    ).fetchone()
    assert plano["criado_em"] is not None and plano["escalado_em"] is None


def test_previsoes_aceita_horas_e_recusa_objeto(conn):
    """E2: clima.previsoes guarda `horas` como array JSON; objeto é recusado."""
    conn.execute("INSERT INTO clima.previsoes (talhao_id, fonte, horas) VALUES (1, 'replay', '[]'::jsonb)")
    linha = conn.execute("SELECT dias, buscada_em FROM clima.previsoes WHERE talhao_id = 1").fetchone()
    assert linha["dias"] == [] and linha["buscada_em"] is not None

    with pytest.raises(CheckViolation):
        with conn.transaction():
            conn.execute("INSERT INTO clima.previsoes (talhao_id, fonte, horas) VALUES (1, 'replay', '{}'::jsonb)")


def test_incidente_nasce_live_e_recusa_origem_invalida(conn):
    """E2: agente.incidentes.origem assume 'live' e só aceita 'live' ou 'replay'."""
    origem = conn.execute(
        """
        INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)
        VALUES (1, '[{"tipo":"chuva"}]'::jsonb, '[]'::jsonb, 'aberto')
        RETURNING origem
        """
    ).fetchone()["origem"]
    assert origem == "live"

    with pytest.raises(CheckViolation):
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status, origem)
                VALUES (2, '[{"tipo":"chuva"}]'::jsonb, '[]'::jsonb, 'aberto', 'simulado')
                """
            )


def test_fatores_aceita_atraso_e_recusa_condicao_desconhecida(conn):
    """E7: conhecimento.fatores_perda aceita 'atraso' (fração por dia de demora) e recusa condição fora do contrato."""
    conn.execute(
        "INSERT INTO conhecimento.fatores_perda (tipo_insumo, condicao, fator, fonte_ou_estimativa) "
        "VALUES ('inseticida', 'atraso', 0.05, 'estimativa: teste')"
    )
    with pytest.raises(CheckViolation):
        with conn.transaction():
            conn.execute(
                "INSERT INTO conhecimento.fatores_perda (tipo_insumo, condicao, fator, fonte_ou_estimativa) "
                "VALUES ('inseticida', 'lavagem', 0.05, 'estimativa: teste')"
            )


def test_observacoes_chave_por_estacao_e_hora_e_fonte_valida(conn):
    """E3: uma linha por (estação, hora); fonte de estação só 'inmet' ou 'fazenda'."""
    conn.execute("INSERT INTO clima.estacoes VALUES ('A1', 'Perto', -17.80, -50.92, 'inmet')")
    conn.execute("INSERT INTO clima.observacoes (estacao, ts, chuva_mm) VALUES ('A1', '2026-11-05T10:00Z', 1.5)")
    with pytest.raises(UniqueViolation):
        with conn.transaction():
            conn.execute("INSERT INTO clima.observacoes (estacao, ts) VALUES ('A1', '2026-11-05T10:00Z')")
    with pytest.raises(CheckViolation):
        with conn.transaction():
            conn.execute("INSERT INTO clima.estacoes VALUES ('A2', 'X', -17.8, -50.9, 'outra')")


def test_011_marca_incidentes_antigos_como_replay_e_e_idempotente(conn):
    """I1: incidente anterior à coluna `origem` vira 'replay'; novo nasce 'live'; re-rodar não muda nada."""
    migracao = (SEED.parent / "011_clima.sql").read_text(encoding="utf-8")
    conn.execute("ALTER TABLE agente.incidentes DROP COLUMN origem")
    antigo = conn.execute(
        """
        INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)
        VALUES (1, '[{"tipo":"chuva"}]'::jsonb, '[]'::jsonb, 'encerrado')
        RETURNING id
        """
    ).fetchone()["id"]

    conn.execute(migracao)
    assert conn.execute(
        "SELECT origem FROM agente.incidentes WHERE id = %s", (antigo,)
    ).fetchone()["origem"] == "replay"

    novo = conn.execute(
        """
        INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)
        VALUES (2, '[{"tipo":"chuva"}]'::jsonb, '[]'::jsonb, 'aberto')
        RETURNING id
        """
    ).fetchone()["id"]

    conn.execute(migracao)
    origens = dict(
        (r["id"], r["origem"]) for r in conn.execute("SELECT id, origem FROM agente.incidentes").fetchall()
    )
    assert origens == {antigo: "replay", novo: "live"}
