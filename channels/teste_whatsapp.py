"""Teste manual do WhatsAppChannel (F4), contra a Cloud API real.

    uv run python -m channels.teste_whatsapp 5564999998888

Manda um plano de exemplo com os botões para o telefone informado (formato internacional, só dígitos). O
telefone precisa estar cadastrado como destinatário de teste na Meta. Fora da janela de 24 h o canal usa o
modelo `terramind_aprovacao`, que precisa estar aprovado. O clique chega ao webhook do `agents`, não a este
script: suba o `agents` com `CHANNEL=whatsapp` para ver a resposta. Ver `docs/whatsapp.md`.
"""

import asyncio
import sys

from channels.base import Resposta, criar_canal
from channels.teste_bot import carregar_env


async def ao_responder_falso(resposta: Resposta) -> None:
    print(f"[ao_responder] {resposta}", flush=True)


async def main(telefone: int) -> None:
    carregar_env()
    canal = criar_canal("whatsapp", ao_responder_falso)
    canal.gerente_padrao = telefone  # o teste não passa por incidente: manda direto para este número
    try:
        await canal.pedir_aprovacao(
            0, 0, "Teste do TerraMind: plano de exemplo (incidente 0, plano 0). Clique em um botão."
        )
        print("Mensagem enviada. O clique chega ao webhook do agents.", flush=True)
    finally:
        await canal.parar()


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdecimal():
        sys.exit("uso: python -m channels.teste_whatsapp <telefone só com dígitos, ex.: 5564999998888>")
    asyncio.run(main(int(sys.argv[1])))
