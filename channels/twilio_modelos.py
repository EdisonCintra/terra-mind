"""Cria os modelos (Content Templates) do WhatsApp no Twilio e pede a aprovação deles (F4).

    uv run python -m channels.twilio_modelos criar
    uv run python -m channels.twilio_modelos aprovar HXxxxxxxxx terramind_aprovacao

`criar` faz dois modelos e imprime os `ContentSid` para o `.env`:

- `terramind_aprovacao`: corpo `{{1}}` (o plano) e 3 botões de resposta rápida, com identificadores fixos
  `SIM`, `OPCOES` e `NAO` (o canal acha o plano pela mensagem respondida; ver `channels/whatsapp.py`);
- `terramind_aviso`: só o corpo `{{1}}`, para alertas e avisos ao operador.

Dentro da janela de 24 h o modelo de botões já funciona sem aprovação. **Fora** da janela, só vale modelo
aprovado pela Meta: `aprovar` envia o pedido. Conta de teste (trial) e Sandbox não aprovam modelo próprio: lá
dá para testar sem criar nenhum, que a aprovação vai como texto e o gerente responde SIM, OPCOES ou NAO.
Ver `docs/whatsapp.md`.
"""

import argparse
import os
import sys
from typing import Any

import httpx

from channels.base import ROTULOS
from channels.teste_bot import carregar_env

URL_CONTENT = "https://content.twilio.com/v1/Content"
NOME_APROVACAO = "terramind_aprovacao"
NOME_AVISO = "terramind_aviso"
CATEGORIA = "UTILITY"
IDIOMA = "pt_BR"


def modelo_aprovacao() -> dict[str, Any]:
    botoes = [{"title": rotulo, "id": resposta} for resposta, rotulo in ROTULOS.items()]
    return {
        "friendly_name": NOME_APROVACAO,
        "language": IDIOMA,
        "variables": {"1": "Plano de exemplo"},
        "types": {"twilio/quick-reply": {"body": "{{1}}", "actions": botoes}},
    }


def modelo_aviso() -> dict[str, Any]:
    return {
        "friendly_name": NOME_AVISO,
        "language": IDIOMA,
        "variables": {"1": "Aviso de exemplo"},
        "types": {"twilio/text": {"body": "{{1}}"}},
    }


def _credenciais() -> tuple[str, str]:
    sid, token = os.environ.get("TWILIO_ACCOUNT_SID", ""), os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        sys.exit("Preencha TWILIO_ACCOUNT_SID e TWILIO_AUTH_TOKEN no .env.")
    return sid, token


def _enviar(http: httpx.Client, url: str, corpo: dict[str, Any], auth: tuple[str, str]) -> dict[str, Any]:
    resposta = http.post(url, json=corpo, auth=auth)
    if not resposta.is_success:
        sys.exit(f"O Twilio recusou (HTTP {resposta.status_code}): {resposta.text[:300]}")
    return resposta.json()


def criar(http: httpx.Client, auth: tuple[str, str]) -> dict[str, str]:
    sids = {}
    for variavel, corpo in (
        ("TWILIO_CONTENT_SID_APROVACAO", modelo_aprovacao()),
        ("TWILIO_CONTENT_SID_AVISO", modelo_aviso()),
    ):
        sids[variavel] = _enviar(http, URL_CONTENT, corpo, auth)["sid"]
    return sids


def aprovar(http: httpx.Client, auth: tuple[str, str], content_sid: str, nome: str) -> dict[str, Any]:
    corpo = {"name": nome, "category": CATEGORIA}
    return _enviar(http, f"{URL_CONTENT}/{content_sid}/ApprovalRequests/whatsapp", corpo, auth)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Modelos do WhatsApp no Twilio (Content API).")
    sub = ap.add_subparsers(dest="comando", required=True)
    sub.add_parser("criar", help="cria os modelos de aprovação e de aviso e imprime os ContentSid")
    pedido = sub.add_parser("aprovar", help="pede à Meta a aprovação de um modelo (precisa de conta paga)")
    pedido.add_argument("content_sid", help="HX... do modelo")
    pedido.add_argument("nome", help=f"nome do modelo, ex.: {NOME_APROVACAO} (minúsculas e _)")
    args = ap.parse_args(argv)
    carregar_env()
    auth = _credenciais()
    with httpx.Client(timeout=30) as http:
        if args.comando == "criar":
            print("Modelos criados. Cole no .env e reinicie o agents:\n")
            for variavel, sid in criar(http, auth).items():
                print(f"{variavel}={sid}")
        else:
            print(aprovar(http, auth, args.content_sid, args.nome))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
