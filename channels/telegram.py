"""Canal Telegram com botões inline e long polling (T8.3, contratos 6 e 7).

Roda dentro do processo `agents`: o lifespan chama `await canal.iniciar()` e, no desligamento,
`await canal.parar()`. Só pode existir um processo fazendo polling por token (erro `Conflict`).
"""

import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from channels.base import AoResponder, DecisaoJaRegistrada, NotificationChannel, Resposta

log = logging.getLogger(__name__)

PREFIXO = "r"  # callback_data: r:{incidente_id}:{plano_id}:{resposta}  (limite do Telegram: 64 bytes)
ROTULOS = {"SIM": "✅ SIM", "NAO": "❌ NÃO", "OPCOES": "🔎 Ver opções"}
ECO = {"SIM": "SIM", "NAO": "NÃO", "OPCOES": "Ver opções"}


def montar_callback(incidente_id: int, plano_id: int, resposta: str) -> str:
    return f"{PREFIXO}:{incidente_id}:{plano_id}:{resposta}"


def ler_callback(data: str) -> Resposta | None:
    """Converte o `callback_data` num `Resposta` (sem `respondido_por`); `None` se não for nosso."""
    partes = (data or "").split(":")
    if len(partes) != 4 or partes[0] != PREFIXO:
        return None
    try:
        return Resposta(int(partes[1]), int(partes[2]), partes[3], "")  # type: ignore[arg-type]
    except ValueError:
        return None


def montar_teclado(incidente_id: int, plano_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(rotulo, callback_data=montar_callback(incidente_id, plano_id, r))
          for r, rotulo in ROTULOS.items()]]
    )


class TelegramChannel(NotificationChannel):
    def __init__(
        self, ao_responder: AoResponder, token: str | None = None, gerente_chat_id: int | None = None
    ):
        self.ao_responder = ao_responder
        gerente = gerente_chat_id if gerente_chat_id is not None else os.environ["GERENTE_CHAT_ID"]
        self.gerente = int(gerente)
        self.app = Application.builder().token(token or os.environ["TELEGRAM_BOT_TOKEN"]).build()
        self.app.add_handler(CallbackQueryHandler(self._ao_clicar, pattern=f"^{PREFIXO}:"))
        self.app.add_error_handler(self._ao_errar)

    async def _ao_errar(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, Conflict):
            log.error(
                "Conflict no getUpdates: outro processo está fazendo polling com este "
                "TELEGRAM_BOT_TOKEN (outro container, a VM ou o teste_bot). Deixe só um ligado."
            )
        else:
            log.error("erro no bot do Telegram", exc_info=context.error)

    async def enviar_alerta(self, texto: str, incidente_id: int | None = None) -> None:
        await self.app.bot.send_message(chat_id=self.gerente, text=texto)

    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None:
        await self.app.bot.send_message(
            chat_id=self.gerente, text=texto, reply_markup=montar_teclado(incidente_id, plano_id)
        )

    async def notificar_operador(self, chat_id: int, texto: str) -> None:
        await self.app.bot.send_message(chat_id=chat_id, text=texto)

    async def _ao_clicar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()  # tira o "relógio" do botão no celular
        decisao = ler_callback(query.data)
        if decisao is None:
            return
        # Remove os botões antes de chamar o agents: evita clique duplo.
        await query.edit_message_reply_markup(reply_markup=None)
        quem = query.from_user.full_name if query.from_user else "gerente"
        resposta = Resposta(decisao.incidente_id, decisao.plano_id, decisao.resposta, quem)
        try:
            await self.ao_responder(resposta)
        except DecisaoJaRegistrada:
            await query.message.reply_text("Decisão já registrada para este plano. Nada foi alterado.")
        except Exception:
            log.exception("falha ao registrar a resposta %s", resposta)
            await query.message.reply_text("Não consegui registrar a resposta. Tente pelo painel.")
        else:
            await query.message.reply_text(f"Resposta registrada: {ECO[resposta.resposta]}")

    async def iniciar(self) -> None:
        """Liga o long polling sem travar o resto do programa."""
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    async def parar(self) -> None:
        if self.app.updater.running:
            await self.app.updater.stop()
        if self.app.running:
            await self.app.stop()
        await self.app.shutdown()
