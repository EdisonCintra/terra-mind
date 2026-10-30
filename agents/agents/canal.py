"""Ponte com o `NotificationChannel` do Felipe (contratos 6 e 7).

Os nós só conhecem o `Canal` abaixo. Em produção, `criar_canal` usa `channels.base.criar_canal`
do Felipe; se o pacote `channels` ainda não estiver instalado, cai num canal que só escreve no log
(o painel continua mostrando a pendência, porque o plano está em `agente.planos`).
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from agents.modelos import Resposta

logger = logging.getLogger(__name__)


class Canal(Protocol):
    async def enviar_alerta(self, texto: str) -> None: ...
    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None: ...
    async def notificar_operador(self, chat_id: int, texto: str) -> None: ...


class CanalLog:
    """Canal sem envio: registra no log. Usado quando `channels` não está instalado."""

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


def criar_canal(nome: str, ao_responder: Callable[[Resposta], Awaitable[Any]]) -> Any:
    try:
        from channels.base import criar_canal as criar_canal_felipe  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("pacote `channels` não encontrado; usando CanalLog (CHANNEL=%s ignorado)", nome)
        return CanalLog()

    async def _ao_responder(r: Any) -> None:
        await ao_responder(normalizar_resposta(r))

    return criar_canal_felipe(nome, _ao_responder)


async def iniciar_canal(canal: Any) -> None:
    iniciar = getattr(canal, "iniciar", None)
    if iniciar is not None:
        await iniciar()


async def parar_canal(canal: Any) -> None:
    app = getattr(canal, "app", None)  # TelegramChannel do Felipe expõe a Application
    parar = getattr(canal, "parar", None)
    if parar is not None:
        await parar()
    elif app is not None and getattr(app, "updater", None) is not None:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
