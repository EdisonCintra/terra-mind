"""Canal Telegram com botões inline e long polling (T8.3, contratos 6 e 7).

Mensagem de texto do gerente (ou de um chat em TELEGRAM_CHATS_BULA) é uma pergunta à bula (G4):
vai para o `ao_perguntar` do `agents` e a resposta volta com a bula e a página.

Destino (F2, contrato 20): cada mensagem vai ao(s) gerente(s) da fazenda do incidente, lidos de
`erp.gerentes` (`channels.destinos`). `GERENTE_CHAT_ID` é só o gerente padrão, para a fazenda que ainda
não tem gerente com chat.

Roda dentro do processo `agents`: o lifespan chama `await canal.iniciar()` e, no desligamento,
`await canal.parar()`. Só pode existir um processo fazendo polling por token (erro `Conflict`).
"""

import asyncio
import logging
import os
from dataclasses import replace

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

from channels import destinos
from channels.base import (
    DECISAO_JA_REGISTRADA,
    ECO,
    PREFIXO,
    ROTULOS,
    AoResponder,
    DecisaoJaRegistrada,
    NotificationChannel,
    ler_callback,
    montar_callback,
)

log = logging.getLogger(__name__)

AJUDA = (
    "Pergunte à bula: escreva sua dúvida citando o produto.\n"
    "Ex.: Posso aplicar Abacus HC com vento de 12 km/h?\n"
    "Ex.: Qual a carência do Brucia no milho?\n"
    "Toda resposta vem com a bula e a página. Se a bula não trouxer o assunto, eu aviso."
)
NAO_AUTORIZADO = "Este número ainda não está liberado para perguntas. Peça ao gerente da fazenda."
SEM_PERGUNTA = "As perguntas à bula ainda não estão ligadas neste bot."

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
        padrao = gerente_chat_id if gerente_chat_id is not None else os.environ.get("GERENTE_CHAT_ID", "")
        token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError(
                "CHANNEL=telegram exige TELEGRAM_BOT_TOKEN preenchido; sem ele use CHANNEL=painel"
            )
        # Gerente padrão: só quando a fazenda do incidente não tem gerente com chat em `erp.gerentes`.
        self.gerente_padrao = int(padrao) if str(padrao).strip() else None
        # Quem pode fazer pergunta livre: os gerentes (do banco e o padrão) e os chats extras de
        # TELEGRAM_CHATS_BULA ("123,456"). Cada pergunta custa uma chamada de LLM; desconhecido não
        # é atendido.
        extras = chats_bula if chats_bula is not None else os.environ.get("TELEGRAM_CHATS_BULA", "")
        self.chats_pergunta = {
            *([self.gerente_padrao] if self.gerente_padrao is not None else []),
            *(int(c) for c in str(extras).replace(" ", "").split(",") if c),
        }
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

    async def _gerentes(self, incidente_id: int | None) -> list[int]:
        """Chats dos gerentes da fazenda do incidente (ou o gerente padrão). `SemDestino` se não houver."""
        return await asyncio.to_thread(destinos.destinos_do_incidente, incidente_id, self.gerente_padrao)

    async def enviar_alerta(self, texto: str, incidente_id: int | None = None) -> None:
        if incidente_id is None:
            incidente_id = destinos.incidente_do_texto(texto)  # "Incidente 7: ..." liga o alerta à fazenda
        for chat in await self._gerentes(incidente_id):
            await self.app.bot.send_message(chat_id=chat, text=texto)

    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None:
        teclado = montar_teclado(incidente_id, plano_id)
        for chat in await self._gerentes(incidente_id):
            await self.app.bot.send_message(chat_id=chat, text=texto, reply_markup=teclado)

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
        resposta = replace(decisao, respondido_por=quem)
        try:
            await self.ao_responder(resposta)
        except DecisaoJaRegistrada:
            await query.message.reply_text(DECISAO_JA_REGISTRADA)
        except Exception:
            log.exception("falha ao registrar a resposta %s", resposta)
            await query.message.reply_text("Não consegui registrar a resposta. Tente pelo painel.")
        else:
            await query.message.reply_text(f"{ECO[resposta.resposta]} por {quem}.")

    async def _pode_perguntar(self, chat_id: int) -> bool:
        if chat_id in self.chats_pergunta:
            return True
        return chat_id in await asyncio.to_thread(destinos.todos_os_chats_de_gerentes)

    async def _ao_pedir_ajuda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat and await self._pode_perguntar(update.effective_chat.id):
            await update.effective_message.reply_text(AJUDA)

    async def _ao_escrever(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Mensagem de texto = pergunta à bula. Roda em segundo plano para não travar os botões."""
        mensagem, chat = update.effective_message, update.effective_chat
        if mensagem is None or chat is None or not (mensagem.text or "").strip():
            return
        if not await self._pode_perguntar(chat.id):
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
