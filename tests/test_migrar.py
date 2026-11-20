"""Runner de migrations (F2): aplica só o que falta, adota banco antigo e não apaga dados."""

import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.errors import CheckViolation

from db import migrar
from tests.conftest import DATABASE_URL

PASTA_REAL = Path(migrar.__file__).parent / "migrations"


def _url(nome: str) -> str:
    return psycopg.conninfo.make_conninfo(DATABASE_URL, dbname=nome)


@pytest.fixture
def banco_vazio():
    """Um banco novo e descartável: o runner nunca toca no banco dos outros testes."""
    nome = f"tm_migrar_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{nome}"')
    try:
        yield _url(nome)
    finally:
        with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{nome}" WITH (FORCE)')


def _migrations(pasta: Path, **arquivos: str) -> Path:
    pasta.mkdir(exist_ok=True)
    for nome, sql in arquivos.items():
        (pasta / f"{nome}.sql").write_text(sql, encoding="utf-8")
    return pasta


def test_banco_novo_recebe_todas_as_migrations_do_projeto_e_o_seed(banco_vazio):
    nomes = migrar.aplicar(banco_vazio)
    assert nomes == sorted(p.name for p in PASTA_REAL.glob("*.sql"))
    with psycopg.connect(banco_vazio) as conn:
        assert conn.execute("SELECT count(*) FROM erp.fazendas").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM erp.talhoes WHERE fazenda_id IS NULL").fetchone()[0] == 0


def test_segunda_execucao_nao_roda_nada_de_novo(banco_vazio, tmp_path):
    pasta = _migrations(tmp_path / "m", **{"001_a": "CREATE TABLE a (x int);"})
    assert migrar.aplicar(banco_vazio, pasta) == ["001_a.sql"]
    assert migrar.aplicar(banco_vazio, pasta) == []  # CREATE TABLE sem IF NOT EXISTS estouraria se rodasse


def test_migration_nova_sobe_sem_apagar_os_dados(banco_vazio, tmp_path):
    pasta = _migrations(tmp_path / "m", **{"001_a": "CREATE TABLE a (x int); INSERT INTO a VALUES (1), (2);"})
    migrar.aplicar(banco_vazio, pasta)
    _migrations(pasta, **{"002_b": "ALTER TABLE a ADD COLUMN y int DEFAULT 7;"})
    assert migrar.aplicar(banco_vazio, pasta) == ["002_b.sql"]
    with psycopg.connect(banco_vazio) as conn:
        assert conn.execute("SELECT x, y FROM a ORDER BY x").fetchall() == [(1, 7), (2, 7)]


def test_migration_com_erro_nao_fica_registrada_nem_aplicada_pela_metade(banco_vazio, tmp_path):
    pasta = _migrations(tmp_path / "m", **{"001_ruim": "CREATE TABLE b (x int); SELECT 1/0;"})
    with pytest.raises(psycopg.errors.DivisionByZero):
        migrar.aplicar(banco_vazio, pasta)
    with psycopg.connect(banco_vazio) as conn:
        assert conn.execute("SELECT to_regclass('b')").fetchone()[0] is None
        assert conn.execute("SELECT count(*) FROM public.migracoes_aplicadas").fetchone()[0] == 0
    # Corrigida a migration, a próxima execução a aplica.
    _migrations(pasta, **{"001_ruim": "CREATE TABLE b (x int);"})
    assert migrar.aplicar(banco_vazio, pasta) == ["001_ruim.sql"]


def test_banco_antigo_sem_registro_e_adotado_e_recebe_so_o_que_faltava(banco_vazio, tmp_path):
    """Banco criado pelo `initdb` (001, 002, 010 e 900, sem registro e sem 003/020): o runner registra
    o que já está lá, aplica 003 e 020 e preserva os dados cadastrados."""
    antigas = tmp_path / "antigas"
    antigas.mkdir()
    for nome in ("001_erp.sql", "002_agente.sql", "010_conhecimento.sql"):
        (antigas / nome).write_text((PASTA_REAL / nome).read_text(encoding="utf-8"), encoding="utf-8")
    # O seed antigo: o do commit anterior ao F2 (sem fazenda_id), reduzido ao que importa para o teste.
    (antigas / "900_seed.sql").write_text(
        "INSERT INTO erp.talhoes (nome, area_ha, cultura, data_plantio, estagio_fenologico, lat, lon)"
        " VALUES ('Talhão Antigo', 10, 'soja', current_date, 'V3', -17.8, -50.9);"
        "INSERT INTO erp.insumos (produto, tipo, unidade, preco_unitario, estoque)"
        " VALUES ('Abacus HC', 'fungicida', 'L', 185, 500);",
        encoding="utf-8",
    )
    with psycopg.connect(banco_vazio, autocommit=True) as conn:  # o que o initdb faria
        for arquivo in sorted(antigas.glob("*.sql")):
            conn.execute(arquivo.read_text(encoding="utf-8"))

    nomes = migrar.aplicar(banco_vazio)  # pasta real: 003 e 020 são novas para este banco

    assert nomes == ["003_fazendas.sql", "020_decisao.sql"]
    with psycopg.connect(banco_vazio) as conn:
        talhao = conn.execute("SELECT nome, fazenda_id FROM erp.talhoes").fetchall()
        assert talhao == [("Talhão Antigo", 1)]
        assert conn.execute("SELECT fazenda_id FROM erp.insumos").fetchone()[0] == 1
        assert conn.execute("SELECT nome FROM erp.fazendas").fetchone()[0] == "Fazenda Exemplo"
        assert conn.execute("SELECT count(*) FROM public.migracoes_aplicadas").fetchone()[0] == 6
    assert migrar.aplicar(banco_vazio) == []


def test_listar_mostra_pendentes_e_aplicadas(banco_vazio, tmp_path):
    pasta = _migrations(tmp_path / "m", **{"001_a": "SELECT 1;", "002_b": "SELECT 2;"})
    migrar.aplicar(banco_vazio, _migrations(tmp_path / "so_a", **{"001_a": "SELECT 1;"}))
    assert migrar.listar(banco_vazio, pasta) == [("001_a.sql", True), ("002_b.sql", False)]


def test_os_nao_mistura_fazendas_nem_aceita_maquina_de_outra(banco_vazio):
    """Contrato 20: a OS é da fazenda do talhão; máquina, operador ou insumo de outra fazenda não passa."""
    migrar.aplicar(banco_vazio)
    with psycopg.connect(banco_vazio, autocommit=True) as conn:
        # Seed: talhão 1 e máquina 1 são da fazenda 1; a máquina 3 é da fazenda 2 (Demo).
        os_id = conn.execute(
            "INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, inicio_previsto, maquina_id)"
            " VALUES (1, 'pulverizacao', now() + interval '3 days', 1) RETURNING id"
        ).fetchone()[0]
        sql = "SELECT fazenda_id FROM erp.ordens_servico WHERE id = %s"
        assert conn.execute(sql, (os_id,)).fetchone()[0] == 1
        with pytest.raises(CheckViolation):
            conn.execute(
                "INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, inicio_previsto, maquina_id)"
                " VALUES (1, 'pulverizacao', now() + interval '4 days', 3)"
            )
        with pytest.raises(CheckViolation):
            conn.execute("UPDATE erp.ordens_servico SET operador_id = 3 WHERE id = %s", (os_id,))
