"""Canal do painel (fallback do Telegram, T8.8).

Não envia nada: o painel web lê o banco. Aqui só o alerta vira linha em `agente.auditoria`
(nó `painel`, ação `enviar_alerta`), para a tela de linha do tempo e o alerta vermelho de
`aguardando_decisao_manual`. O plano é criado e alterado só pelo `agents` (tabela `agente.planos`);
a tela de aprovação lê os planos com `respondido_em IS NULL` e chama `POST /incidentes/{id}/resposta`.
"""

import asyncio
import logging

from channels.base import AoResponder, NotificationChannel
from erp_mcp.db import conectar, registrar_auditoria

log = logging.getLogger(__name__)

NO = "painel"


def _gravar(incidente_id: int | None, acao: str, depois: dict | None, texto: str) -> None:
    with conectar() as conn:
        registrar_auditoria(conn, incidente_id, NO, acao, None, depois, texto)


class PainelChannel(NotificationChannel):
    def __init__(self, ao_responder: AoResponder | None = None):
        # A resposta do gerente chega pelo HTTP do `agents`, não por este objeto.
        self.ao_responder = ao_responder

    async def enviar_alerta(self, texto: str, incidente_id: int | None = None) -> None:
        await asyncio.to_thread(_gravar, incidente_id, "enviar_alerta", None, texto)

    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None:
        # O plano já está em agente.planos (criado pelo agents); o painel mostra os botões.
        log.info("aprovação pendente no painel: incidente %s, plano %s", incidente_id, plano_id)

    async def notificar_operador(self, chat_id: int, texto: str) -> None:
        await asyncio.to_thread(_gravar, None, "notificar_operador", {"chat_id": chat_id}, texto)
