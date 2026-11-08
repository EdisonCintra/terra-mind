"""Testes do `TelegramChannel` sem rede (T8.3): a API do Telegram é trocada por fakes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram import Bot
from telegram.error import Conflict

from channels.base import DecisaoJaRegistrada, Resposta, criar_canal
from channels.telegram import TelegramChannel, ler_callback, montar_callback, montar_teclado


@pytest.fixture
def envio(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(Bot, "send_message", fake)
    return fake


def canal(ao_responder=None):
    return TelegramChannel(ao_responder or AsyncMock(), token="123456:TOKEN-DE-TESTE", gerente_chat_id=42)


def clique(data):
    msg = SimpleNamespace(reply_text=AsyncMock())
    query = SimpleNamespace(
        data=data, message=msg, answer=AsyncMock(), edit_message_reply_markup=AsyncMock(),
        from_user=SimpleNamespace(full_name="Gerente Teste"),
    )
    return SimpleNamespace(callback_query=query), query


def test_callback_data_cabe_em_64_bytes_e_faz_ida_e_volta():
    data = montar_callback(123456, 654321, "OPCOES")
    assert len(data.encode()) <= 64
    r = ler_callback(data)
    assert (r.incidente_id, r.plano_id, r.resposta) == (123456, 654321, "OPCOES")


@pytest.mark.parametrize("data", ["", "x:1:2:SIM", "r:1:2", "r:a:2:SIM", "r:1:2:TALVEZ"])
def test_callback_invalido_e_ignorado(data):
    assert ler_callback(data) is None


def test_teclado_tem_tres_botoes():
    linhas = montar_teclado(7, 3).inline_keyboard
    assert all(len(linha) == 1 for linha in linhas)  # um botão por linha
    botoes = [linha[0] for linha in linhas]
    assert [b.callback_data for b in botoes] == ["r:7:3:SIM", "r:7:3:OPCOES", "r:7:3:NAO"]
    assert [b.text for b in botoes] == ["Aceitar Proposta", "Ver próxima opção", "Recusar Proposta"]


async def test_pedir_aprovacao_manda_botoes_ao_gerente(envio):
    await canal().pedir_aprovacao(7, 3, "Plano X")
    kw = envio.call_args.kwargs
    assert (kw["chat_id"], kw["text"]) == (42, "Plano X")
    assert kw["reply_markup"].inline_keyboard[0][0].callback_data == "r:7:3:SIM"


async def test_alerta_e_operador_sem_botoes(envio):
    c = canal()
    await c.enviar_alerta("atenção")
    await c.notificar_operador(99, "OS remarcada")
    enviados = [(c.kwargs["chat_id"], c.kwargs["text"]) for c in envio.call_args_list]
    assert enviados == [(42, "atenção"), (99, "OS remarcada")]
    assert all(c.kwargs.get("reply_markup") is None for c in envio.call_args_list)


async def test_clique_responde_remove_botoes_e_chama_ao_responder():
    ao_responder = AsyncMock()
    update, query = clique("r:7:3:SIM")
    await canal(ao_responder)._ao_clicar(update, None)
    query.answer.assert_awaited_once()
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    ao_responder.assert_awaited_once_with(Resposta(7, 3, "SIM", "Gerente Teste"))
    query.message.reply_text.assert_awaited_once_with("Proposta aceita por Gerente Teste.")


async def test_resposta_repetida_mostra_decisao_ja_registrada():
    ao_responder = AsyncMock(side_effect=DecisaoJaRegistrada())
    update, query = clique("r:7:3:SIM")
    await canal(ao_responder)._ao_clicar(update, None)
    texto = query.message.reply_text.await_args.args[0]
    assert "já registrada" in texto


async def test_falha_inesperada_avisa_o_gerente_sem_estourar():
    update, query = clique("r:7:3:NAO")
    await canal(AsyncMock(side_effect=RuntimeError("boom")))._ao_clicar(update, None)
    assert "painel" in query.message.reply_text.await_args.args[0]


async def test_clique_de_outro_callback_nao_chama_ao_responder():
    ao_responder = AsyncMock()
    update, query = clique("outro:1")
    await canal(ao_responder)._ao_clicar(update, None)
    query.answer.assert_awaited_once()
    ao_responder.assert_not_awaited()


def test_criar_canal_telegram_le_o_ambiente(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TOKEN-DE-TESTE")
    monkeypatch.setenv("GERENTE_CHAT_ID", "42")
    assert isinstance(criar_canal("telegram", AsyncMock()), TelegramChannel)


async def test_conflict_de_polling_vira_mensagem_clara(caplog):
    contexto = SimpleNamespace(error=Conflict("terminated by other getUpdates request"))
    await canal()._ao_errar(None, contexto)
    assert "outro processo" in caplog.text and "TELEGRAM_BOT_TOKEN" in caplog.text


def test_sem_token_ou_chat_id_o_erro_manda_usar_o_painel(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("GERENTE_CHAT_ID", "")
    with pytest.raises(RuntimeError, match="CHANNEL=painel"):
        criar_canal("telegram", AsyncMock())


# --- G4: pergunta livre à bula ------------------------------------------------------------------
def mensagem(texto, chat_id=42):
    """Update de mensagem de texto; `tarefas` guarda o que o canal mandou rodar em segundo plano."""
    msg = SimpleNamespace(text=texto, reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=msg,
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(full_name="Gerente Teste"),
    )
    tarefas = []
    contexto = SimpleNamespace(application=SimpleNamespace(create_task=tarefas.append))
    return update, contexto, msg, tarefas


async def test_pergunta_do_gerente_roda_em_segundo_plano_e_responde_com_a_pagina():
    c = canal()
    ao_perguntar = AsyncMock(return_value="Não. Vento entre 2 e 10 km/h.\n\nFonte: bula Abacus HC, p. 7")
    c.definir_ao_perguntar(ao_perguntar)
    update, contexto, msg, tarefas = mensagem("Posso aplicar Abacus HC com vento de 12 km/h?")
    await c._ao_escrever(update, contexto)
    assert len(tarefas) == 1  # o handler volta na hora: os botões de aprovação não ficam travados
    ao_perguntar.assert_not_awaited()
    await tarefas[0]
    ao_perguntar.assert_awaited_once_with("Posso aplicar Abacus HC com vento de 12 km/h?", "Gerente Teste")
    textos = [chamada.args[0] for chamada in msg.reply_text.await_args_list]
    assert textos[0] == "Consultando as bulas…" and "p. 7" in textos[1]


async def test_chat_desconhecido_nao_gasta_llm():
    c = canal()
    ao_perguntar = AsyncMock()
    c.definir_ao_perguntar(ao_perguntar)
    update, contexto, msg, tarefas = mensagem("Qual a carência do Brucia?", chat_id=777)
    await c._ao_escrever(update, contexto)
    assert tarefas == [] and "não está liberado" in msg.reply_text.await_args.args[0]


async def test_chats_extras_podem_perguntar():
    c = TelegramChannel(AsyncMock(), token="123456:TOKEN-DE-TESTE", gerente_chat_id=42, chats_bula="7, 8")
    c.definir_ao_perguntar(AsyncMock(return_value="ok"))
    update, contexto, _, tarefas = mensagem("Qual a carência do Brucia?", chat_id=8)
    await c._ao_escrever(update, contexto)
    assert len(tarefas) == 1
    assert c.chats_pergunta == {42, 7, 8}


async def test_sem_pergunta_ligada_ou_texto_curto_explica():
    c = canal()
    update, contexto, msg, tarefas = mensagem("Qual a carência do Brucia?")
    await c._ao_escrever(update, contexto)
    assert tarefas == [] and "não estão ligadas" in msg.reply_text.await_args.args[0]
    c.definir_ao_perguntar(AsyncMock())
    update, contexto, msg, tarefas = mensagem("oi")
    await c._ao_escrever(update, contexto)
    assert tarefas == [] and "Pergunte à bula" in msg.reply_text.await_args.args[0]


async def test_falha_na_pergunta_avisa_sem_estourar():
    c = canal()
    c.definir_ao_perguntar(AsyncMock(side_effect=RuntimeError("boom")))
    update, contexto, msg, tarefas = mensagem("Qual a carência do Brucia?")
    await c._ao_escrever(update, contexto)
    await tarefas[0]
    assert "Tente de novo" in msg.reply_text.await_args.args[0]


async def test_ajuda_mostra_exemplos():
    update, _, msg, _ = mensagem("/ajuda")
    await canal()._ao_pedir_ajuda(update, None)
    assert "Posso aplicar Abacus HC" in msg.reply_text.await_args.args[0]


def test_handlers_de_texto_e_ajuda_registrados():
    from telegram.ext import CommandHandler, MessageHandler

    handlers = canal().app.handlers[0]
    assert any(isinstance(h, MessageHandler) for h in handlers)
    assert any(isinstance(h, CommandHandler) and "ajuda" in h.commands for h in handlers)
