"""F2 (contrato 20): cada mensagem vai ao gerente da fazenda do incidente, não a um chat fixo."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest
from telegram import Bot

from channels import destinos
from channels.destinos import SemDestino
from channels.telegram import TelegramChannel
from tests.conftest import DATABASE_URL

# Seed: fazenda 1 (Exemplo) tem os talhões 1 e 2; fazenda 2 (Demo) tem o talhão 3.


def _sql(sql: str, *params):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        linha = conn.execute(sql, params)
        return linha.fetchone() if linha.description else None


def incidente(talhao_id: int) -> int:
    return _sql(
        "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot)"
        " VALUES (%s, '[]'::jsonb, '[]'::jsonb) RETURNING id",
        talhao_id,
    )[0]


def gerente(fazenda_id: int, chat: int | None, ativo: bool = True) -> None:
    _sql("DELETE FROM erp.gerentes WHERE fazenda_id = %s AND telegram_chat_id IS NULL", fazenda_id)
    _sql(
        "INSERT INTO erp.gerentes (fazenda_id, nome, telegram_chat_id, ativo) VALUES (%s, 'G', %s, %s)",
        fazenda_id,
        chat,
        ativo,
    )


@pytest.fixture(autouse=True)
def sem_gerente_padrao_no_ambiente(monkeypatch):
    """Outros testes (e o `.env` do teste_bot) podem deixar GERENTE_CHAT_ID no ambiente."""
    monkeypatch.setenv("GERENTE_CHAT_ID", "")


@pytest.fixture
def envio(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(Bot, "send_message", fake)
    return fake


def canal(padrao: int | None = None) -> TelegramChannel:
    return TelegramChannel(AsyncMock(), token="123456:TOKEN-DE-TESTE", gerente_chat_id=padrao)


def destinos_enviados(envio) -> list[int]:
    return [c.kwargs["chat_id"] for c in envio.call_args_list]


async def test_aprovacao_vai_so_ao_gerente_da_fazenda_do_incidente(envio):
    gerente(1, 111)
    gerente(2, 222)
    await canal().pedir_aprovacao(incidente(1), 5, "Plano da Fazenda Exemplo")
    assert destinos_enviados(envio) == [111]
    envio.reset_mock()
    await canal().pedir_aprovacao(incidente(3), 6, "Plano da Fazenda Demo")
    assert destinos_enviados(envio) == [222]


async def test_alerta_com_incidente_vai_ao_gerente_da_fazenda(envio):
    gerente(1, 111)
    gerente(2, 222)
    await canal().enviar_alerta("falha", incidente_id=incidente(3))
    assert destinos_enviados(envio) == [222]


async def test_alerta_sem_id_acha_a_fazenda_pelo_texto_do_incidente(envio):
    gerente(1, 111)
    gerente(2, 222)
    inc = incidente(3)
    await canal().enviar_alerta(f"Incidente {inc}: OS 4 segue SUSPENSA")
    assert destinos_enviados(envio) == [222]


async def test_todos_os_gerentes_ativos_da_fazenda_recebem(envio):
    gerente(1, 111)
    _sql("INSERT INTO erp.gerentes (fazenda_id, nome, telegram_chat_id) VALUES (1, 'G2', 112)")
    _sql("INSERT INTO erp.gerentes (fazenda_id, nome, telegram_chat_id, ativo) VALUES (1, 'G3', 113, false)")
    await canal().pedir_aprovacao(incidente(1), 5, "x")
    assert sorted(destinos_enviados(envio)) == [111, 112]


async def test_fazenda_sem_gerente_com_chat_usa_o_padrao(envio):
    gerente(1, 111)
    gerente(2, None)  # a Demo ainda não tem chat
    await canal(padrao=42).pedir_aprovacao(incidente(3), 6, "x")
    assert destinos_enviados(envio) == [42]


async def test_sem_gerente_e_sem_padrao_falha_alto_em_vez_de_sumir(envio):
    gerente(2, None)
    with pytest.raises(SemDestino, match="gerente padrão"):
        await canal().pedir_aprovacao(incidente(3), 6, "x")
    envio.assert_not_awaited()


async def test_operador_continua_indo_ao_chat_dele(envio):
    await canal().notificar_operador(99, "OS remarcada")
    assert destinos_enviados(envio) == [99]


async def test_gerente_de_qualquer_fazenda_pode_perguntar_a_bula():
    gerente(2, 222)
    c = canal()
    c.definir_ao_perguntar(AsyncMock(return_value="ok"))
    tarefas: list = []
    msg = SimpleNamespace(text="Qual a carência do Brucia?", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_message=msg, effective_chat=SimpleNamespace(id=222), effective_user=None
    )
    contexto = SimpleNamespace(application=SimpleNamespace(create_task=lambda c: tarefas.append(c)))
    await c._ao_escrever(update, contexto)
    assert len(tarefas) == 1
    for t in tarefas:
        t.close()  # a pergunta em si não importa aqui


def test_destinos_do_incidente_sem_incidente_so_tem_o_padrao():
    assert destinos.destinos_do_incidente(None, 42) == [42]
    with pytest.raises(SemDestino):
        destinos.destinos_do_incidente(None, None)
