"""Teste manual do TelegramChannel (T8.4), sem o agents.

    uv run python -m channels.teste_bot

Manda uma aprovação com botões ao `GERENTE_CHAT_ID`; clique em SIM, NÃO ou Ver opções e a resposta
aparece aqui no terminal (o `ao_responder` é falso). Ctrl+C encerra. Lembre: só um processo por token
pode fazer polling (erro `Conflict` se houver outro).
"""

import asyncio
import os
from pathlib import Path

from channels.base import Resposta, criar_canal


def carregar_env(caminho: Path = Path(".env")) -> None:
    """Lê o `.env` (a última ocorrência de cada chave vale); variáveis já definidas no ambiente vencem."""
    if not caminho.exists():
        return
    valores: dict[str, str] = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.split("#", 1)[0].strip()
        if "=" in linha:
            chave, valor = linha.split("=", 1)
            valores[chave.strip()] = valor.strip()
    for chave, valor in valores.items():
        os.environ.setdefault(chave, valor)


async def ao_responder_falso(resposta: Resposta) -> None:
    print(f"[ao_responder] {resposta}", flush=True)


async def main() -> None:
    carregar_env()
    canal = criar_canal("telegram", ao_responder_falso)
    await canal.iniciar()
    try:
        await canal.pedir_aprovacao(
            0, 0, "🧪 Teste do TerraMind: plano de exemplo (incidente 0, plano 0).\nClique em um botão."
        )
        print("Mensagem enviada. Clique em um botão no Telegram (Ctrl+C para sair).", flush=True)
        await asyncio.Event().wait()
    finally:
        await canal.parar()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
