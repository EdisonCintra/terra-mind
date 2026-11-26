"""Cria o primeiro gerente (ou mais um) pela linha de comando, antes de haver quem cadastre pelo painel.

    uv run python -m painel.criar_usuario --nome "Maria" --email maria@fazenda.com
    (a senha é pedida sem aparecer na tela; ou use a variável TERRAMIND_SENHA)

No `docker compose`, o painel também cria o primeiro gerente sozinho na subida se o banco ainda não tem
nenhum usuário e `PAINEL_ADMIN_EMAIL` e `PAINEL_ADMIN_SENHA` estiverem no `.env` (`inicializar` abaixo).
"""

import argparse
import getpass
import logging
import os
import sys

from painel import auth

log = logging.getLogger("painel")


def inicializar() -> None:
    """Primeiro acesso: sem usuário e com as variáveis do `.env`, cria o gerente (se já há usuário, nada)."""
    email = os.environ.get("PAINEL_ADMIN_EMAIL", "").strip()
    senha = os.environ.get("PAINEL_ADMIN_SENHA", "")
    if not email or not senha or auth.algum_usuario():
        return
    try:
        auth.criar_primeiro_gerente(
            os.environ.get("PAINEL_ADMIN_NOME", "Gerente").strip() or "Gerente", email, senha
        )
        log.warning(
            "Primeiro gerente criado (%s). Troque a senha em /conta e tire PAINEL_ADMIN_SENHA do .env.", email
        )
    except Exception as e:  # noqa: BLE001 — a subida do painel não pode cair por isso
        log.error("Não consegui criar o primeiro gerente: %s", e)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cria um gerente com acesso a todas as fazendas da organização.")
    ap.add_argument("--nome", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--organizacao", help="nome da organização (padrão: a primeira)")
    args = ap.parse_args(argv)
    senha = os.environ.get("TERRAMIND_SENHA") or getpass.getpass("Senha: ")
    try:
        uid = auth.criar_primeiro_gerente(args.nome, args.email, senha, args.organizacao)
    except ValueError as e:
        print(f"Não criei o usuário: {e}", file=sys.stderr)
        return 1
    print(f"Gerente criado (id {uid}). Entre em /login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
