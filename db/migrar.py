"""Runner de migrations (F2): aplica só as migrations novas e registra as aplicadas.

    uv run python -m db.migrar              # usa DATABASE_URL
    uv run python -m db.migrar --listar     # mostra o que está aplicado e o que falta

Antes dele, os SQL só rodavam quando o volume do Postgres era criado (`docker-entrypoint-initdb.d`), e
qualquer mudança exigia `docker compose down -v`, que apaga o que foi cadastrado. Agora o serviço
`migrar` do `docker-compose.yml` roda este módulo a cada subida, e os demais serviços esperam por ele.

Regras:
- Os arquivos de `db/migrations/*.sql` rodam em ordem alfabética (faixas: Felipe 003–009 e seed 9xx,
  Edison 011–019, Gustavo 020–029). Cada um roda numa transação com o seu registro: ou aplica e
  registra, ou nada.
- O registro fica em `public.migracoes_aplicadas` (arquivo, hash do conteúdo e data).
- Arquivo já aplicado nunca roda de novo. Se o conteúdo mudou depois de aplicado, o runner avisa
  (a correção vai numa migration nova, não na editada).
- Banco criado antes do runner (tem `erp.talhoes` e não tem o registro): as migrations que o
  `initdb` já tinha aplicado entram no registro sem rodar; as outras (ex.: 020, idempotente) rodam.
- Um `pg_advisory_lock` impede dois runners ao mesmo tempo.
"""

import hashlib
import logging
import os
import sys
from pathlib import Path

import psycopg

log = logging.getLogger("db.migrar")

PASTA = Path(__file__).resolve().parent / "migrations"
URL_PADRAO = "postgresql://terramind:terramind@localhost:5432/terramind"
TRAVA = 7_201_106  # chave do pg_advisory_lock (qualquer inteiro fixo)

# O que o `docker-entrypoint-initdb.d` já tinha aplicado antes de existir o runner. A 020 fica de fora
# de propósito: ela só entrou depois, e bancos antigos podem não tê-la (ela é idempotente).
JA_APLICADAS_ANTES_DO_RUNNER = (
    "001_erp.sql",
    "002_agente.sql",
    "010_conhecimento.sql",
    "900_seed.sql",
)

CRIAR_REGISTRO = """
    CREATE TABLE IF NOT EXISTS public.migracoes_aplicadas (
        arquivo     text PRIMARY KEY,
        sha256      text NOT NULL,
        aplicada_em timestamptz NOT NULL DEFAULT now()
    )
"""


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _arquivos(pasta: Path) -> list[Path]:
    return sorted(pasta.glob("*.sql"), key=lambda p: p.name)


def _banco_legado(conn: psycopg.Connection) -> bool:
    """Tem o schema do ERP mas ainda não tem o registro: foi criado pelo `initdb`."""
    tem_erp = conn.execute("SELECT to_regclass('erp.talhoes') IS NOT NULL").fetchone()[0]
    tem_registro = conn.execute("SELECT to_regclass('public.migracoes_aplicadas') IS NOT NULL").fetchone()[0]
    return bool(tem_erp and not tem_registro)


def aplicadas(conn: psycopg.Connection) -> dict[str, str]:
    existe = conn.execute("SELECT to_regclass('public.migracoes_aplicadas') IS NOT NULL").fetchone()[0]
    if not existe:
        return {}
    return dict(conn.execute("SELECT arquivo, sha256 FROM public.migracoes_aplicadas").fetchall())


def aplicar(url: str | None = None, pasta: Path = PASTA) -> list[str]:
    """Aplica as migrations que faltam e devolve os nomes aplicados agora (sem os adotados do legado)."""
    url = url or os.environ.get("DATABASE_URL", URL_PADRAO)
    novas: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (TRAVA,))
        try:
            legado = _banco_legado(conn)
            conn.execute(CRIAR_REGISTRO)
            feitas = aplicadas(conn)
            for arquivo in _arquivos(pasta):
                texto = arquivo.read_text(encoding="utf-8")
                soma = _hash(texto)
                if arquivo.name in feitas:
                    if feitas[arquivo.name] != soma and arquivo.name.startswith("9"):
                        # Seed (9xx) é editado de propósito e só vale em banco novo: não é erro.
                        log.info("%s mudou depois de aplicada (seed): vale só em banco novo", arquivo.name)
                    elif feitas[arquivo.name] != soma:
                        log.warning(
                            "%s mudou depois de aplicada; o runner não a roda de novo. "
                            "Corrija com uma migration nova.",
                            arquivo.name,
                        )
                    continue
                if legado and arquivo.name in JA_APLICADAS_ANTES_DO_RUNNER:
                    conn.execute(
                        "INSERT INTO public.migracoes_aplicadas (arquivo, sha256) VALUES (%s, %s)",
                        (arquivo.name, soma),
                    )
                    log.info("%s: já estava no banco, registrada sem rodar", arquivo.name)
                    continue
                with conn.transaction():
                    conn.execute(texto)
                    conn.execute(
                        "INSERT INTO public.migracoes_aplicadas (arquivo, sha256) VALUES (%s, %s)",
                        (arquivo.name, soma),
                    )
                novas.append(arquivo.name)
                log.info("%s: aplicada", arquivo.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (TRAVA,))
    return novas


def listar(url: str | None = None, pasta: Path = PASTA) -> list[tuple[str, bool]]:
    url = url or os.environ.get("DATABASE_URL", URL_PADRAO)
    with psycopg.connect(url, autocommit=True) as conn:
        feitas = aplicadas(conn)
    return [(a.name, a.name in feitas) for a in _arquivos(pasta)]


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--listar" in argv:
        for nome, feita in listar():
            print(f"{'aplicada' if feita else 'PENDENTE':9} {nome}")
        return 0
    novas = aplicar()
    print(f"{len(novas)} migration(s) aplicada(s)" + (f": {', '.join(novas)}" if novas else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
