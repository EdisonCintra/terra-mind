"""Contrato do canal de notificação (contratos 6 e 7).

O `agents` do Gustavo só conhece este módulo: ele chama `criar_canal(CHANNEL, ao_responder)`,
depois `await canal.iniciar()` no `lifespan` e `await canal.parar()` no desligamento. A variável
`CHANNEL` (`telegram`, `whatsapp` ou `painel`) escolhe a implementação, então trocar de canal no palco não
muda uma linha do fluxo.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Literal, get_args

TipoResposta = Literal["SIM", "NAO", "OPCOES"]
RESPOSTAS_VALIDAS: tuple[str, ...] = get_args(TipoResposta)


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


# Botões de aprovação (iguais no Telegram e no WhatsApp). O identificador do botão leva a decisão:
# r:{incidente_id}:{plano_id}:{resposta}. Cabe nos limites dos dois (Telegram 64 bytes, WhatsApp 256).
PREFIXO = "r"
# Rótulos com até 20 caracteres, o limite do botão do WhatsApp.
ROTULOS = {"SIM": "Aceitar Proposta", "OPCOES": "Ver próxima opção", "NAO": "Recusar Proposta"}
ECO = {"SIM": "Proposta aceita", "NAO": "Proposta recusada", "OPCOES": "Próxima opção solicitada"}
DECISAO_JA_REGISTRADA = "Decisão já registrada para este plano. Nada foi alterado."


def montar_callback(incidente_id: int, plano_id: int, resposta: str) -> str:
    return f"{PREFIXO}:{incidente_id}:{plano_id}:{resposta}"


def ler_callback(data: str) -> "Resposta | None":
    """Converte o identificador do botão numa `Resposta` (sem `respondido_por`); `None` se não for nosso."""
    partes = (data or "").split(":")
    if len(partes) != 4 or partes[0] != PREFIXO:
        return None
    try:
        return Resposta(int(partes[1]), int(partes[2]), partes[3], "")  # type: ignore[arg-type]
    except ValueError:
        return None


class DecisaoJaRegistrada(Exception):
    """O `ao_responder` lança isto quando o plano já tem resposta (o equivalente ao 409 do painel).

    O canal mostra "decisão já registrada" ao gerente, sem tratar como erro.
    """


# Função que o `agents` fornece: recebe a decisão e retoma a sessão do incidente.
# Se o plano já foi respondido, lança `DecisaoJaRegistrada`.
AoResponder = Callable[[Resposta], Awaitable[None]]

# G4 nos canais: recebe (pergunta, quem perguntou) e devolve o texto pronto da resposta, com a
# página da bula. O `agents` liga com `definir_ao_perguntar`; canal sem pergunta livre ignora.
AoPerguntar = Callable[[str, str], Awaitable[str]]


class NotificationChannel(ABC):
    """Molde comum do Telegram e do painel."""

    ao_perguntar: AoPerguntar | None = None
    # Coluna de `erp.operadores` com o contato do operador neste canal: o `agents` a lê para chamar
    # `notificar_operador` (Telegram usa o chat; WhatsApp, o telefone).
    campo_operador: str = "telegram_chat_id"

    def definir_ao_perguntar(self, ao_perguntar: AoPerguntar) -> None:
        """Liga a pergunta livre (Pergunte à bula). Sem isso o canal só notifica e aprova."""
        self.ao_perguntar = ao_perguntar

    @abstractmethod
    async def enviar_alerta(self, texto: str, incidente_id: int | None = None) -> None:
        """Mensagem simples para o gerente, sem botões (decisão manual, escalonamento).

        `incidente_id` é opcional: o painel o usa para ligar o alerta ao incidente na auditoria.
        """

    @abstractmethod
    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None:
        """Mensagem com os botões Aceitar Proposta / Recusar Proposta / Ver próxima opção."""

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
    "whatsapp": ("channels.whatsapp", "WhatsAppChannel"),
    "painel": ("channels.painel", "PainelChannel"),
}


def criar_canal(nome: str, ao_responder: AoResponder) -> NotificationChannel:
    """Cria o canal `nome` (`telegram`, `whatsapp` ou `painel`).

    O `ao_responder` é entregue ao canal; no Telegram o clique do gerente chama essa função.
    """
    try:
        modulo, classe = _CANAIS[nome]
    except KeyError:
        raise ValueError(f"canal desconhecido: {nome!r} (use {', '.join(_CANAIS)})") from None
    return getattr(import_module(modulo), classe)(ao_responder)
