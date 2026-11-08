"""Canal Telegram com botões inline e long polling (T8.3, contratos 6 e 7).

Mensagem de texto do gerente (ou de um chat em TELEGRAM_CHATS_BULA) é uma pergunta à bula (G4):
vai para o `ao_perguntar` do `agents` e a resposta volta com a bula e a página.

Roda dentro do processo `agents`: o lifespan chama `await canal.iniciar()` e, no desligamento,
`await canal.parar()`. Só pode existir um processo fazendo polling por token (erro `Conflict`).
"""

import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from channels.base import AoResponder, DecisaoJaRegistrada, NotificationChannel, Resposta

log = logging.getLogger(__name__)

AJUDA = (
    "Pergunte à bula: escreva sua dúvida citando o produto.\n"
    "Ex.: Posso aplicar Abacus HC com vento de 12 km/h?\n"
    "Ex.: Qual a carência do Brucia no milho?\n"
    "Toda resposta vem com a bula e a página. Se a bula não trouxer o assunto, eu aviso."
)
NAO_AUTORIZADO = "Este número ainda não está liberado para perguntas. Peça ao gerente da fazenda."
SEM_PERGUNTA = "As perguntas à bula ainda não estão ligadas neste bot."

PREFIXO = "r"  # callback_data: r:{incidente_id}:{plano_id}:{resposta}  (limite do Telegram: 64 bytes)
ROTULOS = {"SIM": "Aceitar Proposta", "OPCOES": "Ver próxima opção", "NAO": "Recusar Proposta"}
ECO = {"SIM": "Proposta aceita", "NAO": "Proposta recusada", "OPCOES": "Próxima opção solicitada"}


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
    # Um botão por linha: mais fácil de tocar no celular.
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(rotulo, callback_data=montar_callback(incidente_id, plano_id, r))]
            for r, rotulo in ROTULOS.items()
        ]
    )


class TelegramChannel(NotificationChannel):
    def __init__(
        self,
        ao_responder: AoResponder,
        token: str | None = None,
        gerente_chat_id: int | None = None,
        chats_bula: str | None = None,
    ):
        self.ao_responder = ao_responder
        gerente = gerente_chat_id if gerente_chat_id is not None else os.environ.get("GERENTE_CHAT_ID", "")
        token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token or not gerente:
            raise RuntimeError(
                "CHANNEL=telegram exige TELEGRAM_BOT_TOKEN e GERENTE_CHAT_ID preenchidos; "
                "sem eles use CHANNEL=painel"
            )
        self.gerente = int(gerente)
        # Quem pode fazer pergunta livre: o gerente e os chats extras de TELEGRAM_CHATS_BULA
        # ("123,456"). Cada pergunta custa uma chamada de LLM; desconhecido não é atendido.
        extras = chats_bula if chats_bula is not None else os.environ.get("TELEGRAM_CHATS_BULA", "")
        self.chats_pergunta = {self.gerente, *(int(c) for c in str(extras).replace(" ", "").split(",") if c)}
        self.app = Application.builder().token(token).build()
        self.app.add_handler(CallbackQueryHandler(self._ao_clicar, pattern=f"^{PREFIXO}:"))
        self.app.add_handler(CommandHandler(["start", "ajuda"], self._ao_pedir_ajuda))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._ao_escrever))
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
            await query.message.reply_text(f"{ECO[resposta.resposta]} por {quem}.")

    async def _ao_pedir_ajuda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat and update.effective_chat.id in self.chats_pergunta:
            await update.effective_message.reply_text(AJUDA)

    async def _ao_escrever(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Mensagem de texto = pergunta à bula. Roda em segundo plano para não travar os botões."""
        mensagem, chat = update.effective_message, update.effective_chat
        if mensagem is None or chat is None or not (mensagem.text or "").strip():
            return
        if chat.id not in self.chats_pergunta:
            log.warning("pergunta de chat não autorizado ignorada: %s", chat.id)
            await mensagem.reply_text(NAO_AUTORIZADO)
            return
        if self.ao_perguntar is None:
            await mensagem.reply_text(SEM_PERGUNTA)
            return
        pergunta = mensagem.text.strip()
        if len(pergunta) < 3:
            await mensagem.reply_text(AJUDA)
            return
        quem = update.effective_user.full_name if update.effective_user else str(chat.id)
        context.application.create_task(self._responder_pergunta(mensagem, pergunta, quem))

    async def _responder_pergunta(self, mensagem, pergunta: str, quem: str) -> None:
        await mensagem.reply_text("Consultando as bulas…")
        try:
            texto = await self.ao_perguntar(pergunta, quem)
        except Exception:
            log.exception("falha ao responder a pergunta à bula")
            texto = "Não consegui consultar as bulas agora. Tente de novo em instantes."
        await mensagem.reply_text(texto)

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
