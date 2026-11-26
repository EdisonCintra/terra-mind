"""Teste manual do WhatsAppChannel (F4), contra o Twilio real (Sandbox ou número aprovado).

    uv run python -m channels.teste_whatsapp 5564999998888

Manda um plano de exemplo para o telefone informado (formato internacional, só dígitos).

No Sandbox, o telefone tem de ter entrado antes ("join <palavra>" para o número do Sandbox) e, para a
mensagem livre valer, ter mandado uma mensagem nas últimas 24 h.

O clique ou a palavra SIM, OPCOES ou NAO chega ao webhook do `agents`, não a este script. Para ver a
decisão de verdade, suba o `agents` com `CHANNEL=whatsapp` e dispare um incidente pelo painel
("Simular chuva"). Ver `docs/whatsapp.md`.
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
            0, 0, "Teste do TerraMind: plano de exemplo (incidente 0, plano 0). Responda para testar a volta."
        )
        print("Mensagem enviada. A resposta chega ao webhook do agents.", flush=True)
    finally:
        await canal.parar()


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdecimal():
        sys.exit("uso: python -m channels.teste_whatsapp <telefone só com dígitos, ex.: 5564999998888>")
    asyncio.run(main(int(sys.argv[1])))
