"""Testes do contrato `channels.base` (T8.2)."""

import sys
import types

import pytest

from channels.base import NotificationChannel, Resposta, criar_canal


class CanalFalso(NotificationChannel):
    def __init__(self, ao_responder=None):
        self.ao_responder = ao_responder
        self.enviados: list[tuple] = []

    async def enviar_alerta(self, texto):
        self.enviados.append(("alerta", texto))

    async def pedir_aprovacao(self, incidente_id, plano_id, texto):
        self.enviados.append(("aprovacao", incidente_id, plano_id, texto))

    async def notificar_operador(self, chat_id, texto):
        self.enviados.append(("operador", chat_id, texto))


def test_resposta_aceita_as_tres_decisoes_e_recusa_outras():
    for r in ("SIM", "NAO", "OPCOES"):
        assert Resposta(1, 2, r, "gerente").resposta == r
    with pytest.raises(ValueError, match="resposta inválida"):
        Resposta(1, 2, "TALVEZ", "gerente")


def test_resposta_tem_os_campos_que_o_agents_le():
    r = Resposta(incidente_id=7, plano_id=3, resposta="SIM", respondido_por="Fellipe")
    assert (r.incidente_id, r.plano_id, r.resposta, r.respondido_por) == (7, 3, "SIM", "Fellipe")


def test_interface_abstrata_nao_instancia():
    with pytest.raises(TypeError):
        NotificationChannel()


async def test_iniciar_e_parar_existem_por_padrao():
    """O `agents` chama `iniciar()` e `parar()` em qualquer canal."""
    canal = CanalFalso()
    assert await canal.iniciar() is None
    assert await canal.parar() is None


def test_criar_canal_recusa_nome_desconhecido():
    with pytest.raises(ValueError, match="canal desconhecido: 'whatsapp'"):
        criar_canal("whatsapp", lambda r: None)


def test_criar_canal_entrega_o_ao_responder_a_implementacao(monkeypatch):
    modulo = types.ModuleType("channels.telegram")
    modulo.TelegramChannel = CanalFalso
    monkeypatch.setitem(sys.modules, "channels.telegram", modulo)

    async def ao_responder(r):
        return None

    canal = criar_canal("telegram", ao_responder)
    assert isinstance(canal, CanalFalso) and canal.ao_responder is ao_responder
