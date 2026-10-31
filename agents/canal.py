"""Ponte com o `NotificationChannel` do Felipe (`channels.base`, contratos 6 e 7).

Os nós só conhecem o `Canal` abaixo. `criar_canal` monta o canal do Felipe (`CHANNEL` = `telegram`
ou `painel`) com o `ao_responder` do Orquestrador; `CanalLog` é só o canal provisório enquanto o
serviço ainda está subindo.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from agents.modelos import Resposta
from channels.base import DecisaoJaRegistrada, NotificationChannel
from channels.base import criar_canal as criar_canal_felipe

logger = logging.getLogger(__name__)


class Canal(Protocol):
    async def enviar_alerta(self, texto: str) -> None: ...
    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None: ...
    async def notificar_operador(self, chat_id: int, texto: str) -> None: ...


class CanalLog:
    """Canal sem envio: registra no log. Provisório enquanto o canal real não foi montado."""

    async def enviar_alerta(self, texto):
        logger.warning("[alerta ao gerente] %s", texto)

    async def pedir_aprovacao(self, incidente_id, plano_id, texto):
        logger.warning("[aprovação pendente] incidente=%s plano=%s\n%s", incidente_id, plano_id, texto)

    async def notificar_operador(self, chat_id, texto):
        logger.warning("[operador %s] %s", chat_id, texto)

    async def iniciar(self) -> None:
        return None

    async def parar(self) -> None:
        return None


def normalizar_resposta(r: Any) -> Resposta:
    """Aceita o `Resposta` do Felipe (ou qualquer objeto com os mesmos campos)."""
    return Resposta(
        incidente_id=int(r.incidente_id),
        plano_id=int(r.plano_id),
        resposta=r.resposta,
        respondido_por=str(r.respondido_por),
    )


def criar_canal(nome: str, ao_responder: Callable[[Resposta], Awaitable[str]]) -> NotificationChannel:
    """Canal do Felipe. Resposta repetida vira `DecisaoJaRegistrada` (o 409 do Telegram, contrato 6)."""

    async def _ao_responder(r: Any) -> None:
        resultado = await ao_responder(normalizar_resposta(r))
        if resultado == "repetida":
            raise DecisaoJaRegistrada(f"plano {r.plano_id} já respondido")
        if resultado == "desconhecida":
            logger.warning(
                "resposta para plano desconhecido: incidente %s, plano %s", r.incidente_id, r.plano_id
            )

    return criar_canal_felipe(nome, _ao_responder)
