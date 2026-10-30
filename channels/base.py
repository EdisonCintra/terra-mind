"""Contrato do canal de notificação (contratos 6 e 7).

O `agents` do Gustavo só conhece este módulo: ele chama `criar_canal(CHANNEL, ao_responder)`,
depois `await canal.iniciar()` no `lifespan` e `await canal.parar()` no desligamento. A variável
`CHANNEL` (`telegram` ou `painel`) escolhe a implementação, então trocar de canal no palco não
muda uma linha do fluxo.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Literal

TipoResposta = Literal["SIM", "NAO", "OPCOES"]
RESPOSTAS_VALIDAS: tuple[str, ...] = ("SIM", "NAO", "OPCOES")


@dataclass(frozen=True)
class Resposta:
    """Decisão do gerente sobre um plano (contrato 6)."""

    incidente_id: int
    plano_id: int
    resposta: TipoResposta
    respondido_por: str

    def __post_init__(self) -> None:
        if self.resposta not in RESPOSTAS_VALIDAS:
            raise ValueError(f"resposta inválida: {self.resposta!r} (use {', '.join(RESPOSTAS_VALIDAS)})")


# Função que o `agents` fornece: recebe a decisão e retoma a sessão do incidente.
AoResponder = Callable[[Resposta], Awaitable[None]]


class NotificationChannel(ABC):
    """Molde comum do Telegram e do painel."""

    @abstractmethod
    async def enviar_alerta(self, texto: str) -> None:
        """Mensagem simples para o gerente, sem botões (decisão manual, escalonamento)."""

    @abstractmethod
    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None:
        """Mensagem com as opções SIM / NÃO / Ver opções."""

    @abstractmethod
    async def notificar_operador(self, chat_id: int, texto: str) -> None:
        """Aviso para o operador da máquina."""

    async def iniciar(self) -> None:
        """Liga o que o canal precisar (ex.: long polling). Por padrão, nada."""
        return None

    async def parar(self) -> None:
        """Desliga o que `iniciar` ligou. Por padrão, nada."""
        return None


# nome do canal -> (módulo, classe). O import é tardio: `painel` não precisa do pacote do Telegram.
_CANAIS: dict[str, tuple[str, str]] = {
    "telegram": ("channels.telegram", "TelegramChannel"),
    "painel": ("channels.painel", "PainelChannel"),
}


def criar_canal(nome: str, ao_responder: AoResponder) -> NotificationChannel:
    """Cria o canal `nome` (`telegram` ou `painel`).

    O `ao_responder` é entregue ao canal; no Telegram o clique do gerente chama essa função.
    """
    try:
        modulo, classe = _CANAIS[nome]
    except KeyError:
        raise ValueError(f"canal desconhecido: {nome!r} (use {', '.join(_CANAIS)})") from None
    return getattr(import_module(modulo), classe)(ao_responder)
