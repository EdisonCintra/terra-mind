"""Canal do terminal: a conversa fica no agents e o chat (`channels.chat`) conversa com ela por HTTP."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from channels.base import DecisaoJaRegistrada, criar_canal
from channels.chat import formatar
from channels.terminal import TerminalChannel, interpretar


class Gerente:
    """O `ao_responder` do agents, com a lista de planos pendentes que o banco teria."""

    def __init__(self, pendentes=None, ja_respondidos=()):
        self.pendentes = list(pendentes or [])
        self.ja_respondidos = set(ja_respondidos)
        self.respostas = []

    async def ao_responder(self, r):
        if r.plano_id in self.ja_respondidos:
            raise DecisaoJaRegistrada()
        self.respostas.append(r)

    async def buscar(self):
        return self.pendentes


def canal(gerente):
    return TerminalChannel(gerente.ao_responder, buscar_pendentes=gerente.buscar)


def textos(c, de="terramind"):
    return [m.texto for m in c.historico if m.de == de]


@pytest.mark.parametrize(
    ("linha", "esperado"),
    [
        ("sim", ("decisao", "SIM", None)),
        ("  Não ", ("decisao", "NAO", None)),
        ("OPÇÕES", ("decisao", "OPCOES", None)),
        ("sim 12", ("decisao", "SIM", 12)),
        ("nao #3", ("decisao", "NAO", 3)),
        ("pendentes", ("pendentes", None, None)),
        ("?", ("ajuda", None, None)),
        ("", ("vazio", None, None)),
        ("sim, pode aplicar com 10 km/h?", ("pergunta", None, None)),
        ("qual o limite de vento do Abacus HC?", ("pergunta", None, None)),
    ],
)
def test_interpreta_comandos_sem_acento_nem_maiuscula(linha, esperado):
    assert interpretar(linha) == esperado


def test_criar_canal_conhece_o_terminal():
    assert isinstance(criar_canal("terminal", Gerente().ao_responder), TerminalChannel)


async def test_aprovacao_entra_na_conversa_com_os_comandos():
    c = canal(Gerente())
    await c.pedir_aprovacao(7, 3, "Suspendi a OS 1. Proposta: 05/11 06:00.")
    [m] = c.historico
    assert (m.de, m.tipo, m.titulo) == ("terramind", "aprovacao", "Aprovação · incidente 7 · plano 3")
    assert "Proposta: 05/11 06:00." in m.texto and "sim 3" in m.texto


async def test_decisao_sem_numero_responde_o_plano_mais_recente():
    g = Gerente(pendentes=[(2, 5), (3, 7)])
    c = canal(g)
    await c.processar("sim")
    [r] = g.respostas
    assert (r.incidente_id, r.plano_id, r.resposta, r.respondido_por) == (7, 3, "SIM", "terminal")
    assert textos(c) == ["Proposta aceita · plano 3 · incidente 7"]


async def test_decisao_com_numero_acha_o_incidente_do_plano():
    g = Gerente(pendentes=[(2, 5), (3, 7)])
    await canal(g).processar("opcoes 2")
    assert [(r.incidente_id, r.plano_id, r.resposta) for r in g.respostas] == [(5, 2, "OPCOES")]


async def test_plano_inexistente_ou_sem_pendentes_nao_chama_o_agents():
    g = Gerente(pendentes=[(2, 5)])
    c = canal(g)
    await c.processar("sim 99")
    g.pendentes = []
    await c.processar("nao")
    assert g.respostas == []
    assert textos(c) == ["O plano 99 não existe ou já foi respondido.", "Nenhum plano esperando resposta."]


async def test_resposta_repetida_avisa_sem_erro():
    c = canal(Gerente(pendentes=[(3, 7)], ja_respondidos={3}))
    await c.processar("sim")
    assert textos(c) == ["Decisão já registrada para o plano 3. Nada foi alterado."]


async def test_texto_livre_vai_para_o_assistente_com_o_contato_do_terminal():
    c = canal(Gerente())
    perguntas = []

    async def ao_perguntar(pergunta, quem, contato=None):
        perguntas.append((pergunta, quem, contato))
        return "Fonte: bula Abacus HC, p. 7"

    c.definir_ao_perguntar(ao_perguntar)
    await c.processar("Qual o limite de chuva do Abacus HC?")
    assert perguntas == [("Qual o limite de chuva do Abacus HC?", "terminal", "terminal")]
    assert [(m.tipo, m.texto) for m in c.historico] == [("bula", "Fonte: bula Abacus HC, p. 7")]


async def test_erro_ao_tratar_mensagem_vira_mensagem_de_erro():
    async def quebra():
        raise RuntimeError("banco fora")

    c = TerminalChannel(Gerente().ao_responder, buscar_pendentes=quebra)
    await c.processar("pendentes")
    [m] = c.historico
    assert m.tipo == "erro" and "banco fora" in m.texto


async def test_mensagem_do_gerente_entra_na_hora_e_a_resposta_chega_depois():
    g = Gerente(pendentes=[(3, 7)])
    c = canal(g)
    enviada = await c.receber("sim", origem="janela-1")
    assert (enviada.de, enviada.texto, enviada.origem) == ("voce", "sim", "janela-1")
    novas = await c.mensagens(depois=enviada.id, espera_s=2)  # long polling: acorda com a resposta
    assert [m.tipo for m in novas] == ["decisao"]
    assert [r.plano_id for r in g.respostas] == [3]


async def test_long_polling_sem_novidade_devolve_vazio_no_prazo():
    c = canal(Gerente())
    assert await asyncio.wait_for(c.mensagens(depois=0, espera_s=0.05), 1) == []


async def test_alerta_que_chega_durante_a_espera_acorda_o_chat():
    c = canal(Gerente())
    espera = asyncio.create_task(c.mensagens(depois=0, espera_s=5))
    await asyncio.sleep(0.05)
    await c.enviar_alerta("Incidente 7: chuva de 80 mm.", 7)
    [m] = await asyncio.wait_for(espera, 1)
    assert (m.tipo, m.titulo) == ("alerta", "Alerta · incidente 7")


# ------------------------------------------------------------------------------ rotas no agents


class _Deps:
    def __init__(self, canal_em_uso, token=None):
        self.canal = canal_em_uso
        self.settings = type("S", (), {"agents_token": token})()


class _Orq:
    def __init__(self, deps):
        self.deps = deps

    async def aguardar(self):
        return None


def cliente(canal_em_uso, token=None):
    from agents.api import Servicos, criar_api

    async def fabrica():
        return Servicos(orquestrador=_Orq(_Deps(canal_em_uso, token)))

    return TestClient(criar_api(fabrica))


def test_rotas_do_chat_conversam_com_o_canal():
    g = Gerente(pendentes=[(3, 7)])
    with cliente(canal(g)) as http:
        enviada = http.post("/terminal/mensagens", json={"texto": "sim", "origem": "janela-1"}).json()
        assert enviada["de"] == "voce" and enviada["origem"] == "janela-1"
        r = http.get("/terminal/mensagens", params={"depois": 0, "espera": 2}).json()
        assert r["instancia"]
        assert [m["de"] for m in r["mensagens"]][:1] == ["voce"]
    assert [x.plano_id for x in g.respostas] == [3]


def test_rotas_do_chat_nao_existem_com_outro_canal():
    with cliente(object()) as http:
        assert http.get("/terminal/mensagens").status_code == 404
        assert http.post("/terminal/mensagens", json={"texto": "sim"}).status_code == 404


def test_rotas_do_chat_exigem_o_token_quando_configurado():
    with cliente(canal(Gerente()), token="segredo") as http:
        assert http.get("/terminal/mensagens").status_code == 401
        ok = http.get("/terminal/mensagens", headers={"X-Agents-Token": "segredo"})
        assert ok.status_code == 200


# ------------------------------------------------------------------------------ janela do chat


def test_formatar_escapa_o_texto_e_recua_as_linhas():
    m = {"de": "terramind", "tipo": "aprovacao", "titulo": "Aprovação · plano 3", "texto": "a < b\nlinha 2"}
    markup = formatar({**m, "ts": "2026-11-27T14:32:00-03:00"})
    assert "[14:32]" in markup and "TerraMind · Aprovação · plano 3" in markup
    assert "a &lt; b" in markup and "\n        linha 2" in markup
