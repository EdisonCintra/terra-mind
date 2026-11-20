"""WhatsApp (F4): envio com janela de 24 h, botões, webhook assinado e destino por fazenda.

A Cloud API é simulada com `httpx.MockTransport`; o banco é o Postgres real dos outros testes.
Seed: fazenda 1 tem os talhões 1 e 2; a Fazenda Demo (2) tem o talhão 3.
"""

import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from agents.api import Servicos, criar_api
from channels import destinos
from channels.base import DecisaoJaRegistrada, Resposta, criar_canal
from channels.destinos import SemDestino
from channels.whatsapp import ERRO_FORA_DA_JANELA, WhatsAppChannel, achatar
from tests.conftest import DATABASE_URL

GERENTE_EXEMPLO = 5564999990001
GERENTE_DEMO = 5564999990002
OPERADOR = 5564999990003
SEGREDO = "segredo-do-app"


def _sql(sql: str, *params):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        linha = conn.execute(sql, params)
        return linha.fetchone() if linha.description else None


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    for chave in ("WHATSAPP_GERENTE_TELEFONE", "WHATSAPP_NUMEROS_BULA", "WHATSAPP_APP_SECRET"):
        monkeypatch.delenv(chave, raising=False)


class Meta:
    """A Graph API de mentira: guarda o que o canal mandou e pode recusar por janela fechada."""

    def __init__(self):
        self.enviados: list[dict] = []
        self.recusar_livre_com = None  # código de erro a devolver para mensagem que não é de modelo

    def __call__(self, request: httpx.Request) -> httpx.Response:
        corpo = json.loads(request.content)
        assert request.headers["Authorization"] == "Bearer TOKEN-DE-TESTE"
        if self.recusar_livre_com and corpo["type"] != "template":
            return httpx.Response(400, json={"error": {"code": self.recusar_livre_com, "message": "janela"}})
        self.enviados.append(corpo)
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    def tipos(self) -> list[str]:
        return [m["type"] for m in self.enviados]


@pytest.fixture
def meta():
    return Meta()


def canal(meta, ao_responder=None, **kw) -> WhatsAppChannel:
    kw.setdefault("app_secret", SEGREDO)
    kw.setdefault("verify_token", "verifica")
    return WhatsAppChannel(
        ao_responder or AsyncMock(),
        token="TOKEN-DE-TESTE",
        phone_number_id="1234567890",
        cliente=httpx.AsyncClient(transport=httpx.MockTransport(meta)),
        **kw,
    )


def gerente(fazenda_id: int, telefone: int | None, ativo: bool = True) -> None:
    _sql("DELETE FROM erp.gerentes WHERE fazenda_id = %s", fazenda_id)
    _sql(
        "INSERT INTO erp.gerentes (fazenda_id, nome, whatsapp_telefone, ativo) VALUES (%s, 'G', %s, %s)",
        fazenda_id,
        telefone,
        ativo,
    )


def incidente(talhao_id: int) -> int:
    return _sql(
        "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot)"
        " VALUES (%s, '[]'::jsonb, '[]'::jsonb) RETURNING id",
        talhao_id,
    )[0]


def abrir_janela(telefone: int, ha_horas: float = 0) -> None:
    _sql(
        "INSERT INTO agente.whatsapp_janelas (telefone, ultima_mensagem_em)"
        " VALUES (%s, now() - make_interval(secs => %s))"
        " ON CONFLICT (telefone) DO UPDATE SET ultima_mensagem_em = EXCLUDED.ultima_mensagem_em",
        telefone,
        ha_horas * 3600,
    )


# --- mensagens recebidas (formato do webhook da Meta) ---------------------------------------------
def webhook(*mensagens: dict, de: int = GERENTE_EXEMPLO, nome: str = "Maria Gerente") -> dict:
    mensagens = tuple({"from": str(de), "timestamp": "1", **m} for m in mensagens)
    contatos = [{"wa_id": str(de), "profile": {"name": nome}}]
    valor = {"messaging_product": "whatsapp", "contacts": contatos, "messages": list(mensagens)}
    mudanca = {"field": "messages", "value": valor}
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [mudanca]}]}


def clique(botao: str, id_: str = "wamid.1") -> dict:
    resposta = {"id": botao, "title": "x"}
    interativo = {"type": "button_reply", "button_reply": resposta}
    return {"id": id_, "type": "interactive", "interactive": interativo}


def clique_do_modelo(payload: str, id_: str = "wamid.2") -> dict:
    return {"id": id_, "type": "button", "button": {"text": "Aceitar Proposta", "payload": payload}}


def texto(corpo: str, id_: str = "wamid.3") -> dict:
    return {"id": id_, "type": "text", "text": {"body": corpo}}


def respostas_ao(meta, telefone: int) -> list[str]:
    return [m["text"]["body"] for m in meta.enviados if m["type"] == "text" and m["to"] == str(telefone)]


# --- envio -----------------------------------------------------------------------------------------
async def test_dentro_da_janela_a_aprovacao_vai_com_os_tres_botoes_ao_gerente_da_fazenda(meta):
    gerente(1, GERENTE_EXEMPLO)
    gerente(2, GERENTE_DEMO)
    abrir_janela(GERENTE_DEMO)
    inc = incidente(3)  # Talhão Demo
    await canal(meta).pedir_aprovacao(inc, 6, "Plano da Fazenda Demo")
    (msg,) = meta.enviados
    assert msg["to"] == str(GERENTE_DEMO) and msg["type"] == "interactive"
    assert msg["interactive"]["body"]["text"] == "Plano da Fazenda Demo"
    botoes = [b["reply"] for b in msg["interactive"]["action"]["buttons"]]
    assert [b["id"] for b in botoes] == [f"r:{inc}:6:SIM", f"r:{inc}:6:OPCOES", f"r:{inc}:6:NAO"]
    assert [b["title"] for b in botoes] == ["Aceitar Proposta", "Ver próxima opção", "Recusar Proposta"]
    assert all(len(b["title"]) <= 20 and len(b["id"]) <= 256 for b in botoes)


async def test_fora_da_janela_a_aprovacao_vai_como_modelo_com_tres_respostas_rapidas(meta):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO, ha_horas=30)  # última mensagem há 30 h: fechada
    inc = incidente(1)
    await canal(meta).pedir_aprovacao(inc, 5, "Plano:\nremarcar a OS 1\n\n\nObrigado")
    (msg,) = meta.enviados
    assert msg["type"] == "template" and msg["template"]["name"] == "terramind_aprovacao"
    assert msg["template"]["language"] == {"code": "pt_BR"}
    corpo, *botoes = msg["template"]["components"]
    assert corpo["parameters"][0]["text"] == "Plano: · remarcar a OS 1 · Obrigado"  # sem quebra de linha
    assert [(b["sub_type"], b["index"], b["parameters"][0]["payload"]) for b in botoes] == [
        ("quick_reply", "0", f"r:{inc}:5:SIM"),
        ("quick_reply", "1", f"r:{inc}:5:OPCOES"),
        ("quick_reply", "2", f"r:{inc}:5:NAO"),
    ]


async def test_contato_novo_sem_janela_recebe_modelo(meta):
    gerente(1, GERENTE_EXEMPLO)
    await canal(meta).pedir_aprovacao(incidente(1), 5, "x")
    assert meta.tipos() == ["template"]


async def test_se_a_meta_recusa_a_mensagem_livre_por_janela_cai_para_o_modelo(meta):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO)  # o banco acha que está aberta, a Meta discorda
    meta.recusar_livre_com = ERRO_FORA_DA_JANELA
    await canal(meta).pedir_aprovacao(incidente(1), 5, "x")
    assert meta.tipos() == ["template"]


async def test_outro_erro_da_meta_nao_vira_modelo_e_sobe(meta):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO)
    meta.recusar_livre_com = 190  # token inválido
    with pytest.raises(Exception, match="recusou o envio"):
        await canal(meta).pedir_aprovacao(incidente(1), 5, "x")
    assert meta.enviados == []


async def test_corpo_longo_vai_como_texto_e_os_botoes_com_corpo_curto(meta):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO)
    longo = "Plano detalhado. " * 100  # > 1024
    await canal(meta).pedir_aprovacao(incidente(1), 5, longo)
    assert meta.tipos() == ["text", "interactive"]
    assert meta.enviados[0]["text"]["body"] == longo
    assert len(meta.enviados[1]["interactive"]["body"]["text"]) < 1024


async def test_alerta_do_incidente_vai_ao_gerente_da_fazenda_dentro_e_fora_da_janela(meta):
    gerente(1, GERENTE_EXEMPLO)
    gerente(2, GERENTE_DEMO)
    abrir_janela(GERENTE_DEMO)
    c = canal(meta)
    await c.enviar_alerta("falha na Demo", incidente_id=incidente(3))
    await c.enviar_alerta(f"Incidente {incidente(1)}: OS 1 segue SUSPENSA")  # sem id: acha pelo texto
    assert [(m["to"], m["type"]) for m in meta.enviados] == [
        (str(GERENTE_DEMO), "text"),
        (str(GERENTE_EXEMPLO), "template"),
    ]
    assert meta.enviados[1]["template"]["name"] == "terramind_aviso"


async def test_todos_os_gerentes_da_fazenda_recebem_e_um_que_falha_nao_impede_os_outros():
    gerente(1, GERENTE_EXEMPLO)
    _sql("INSERT INTO erp.gerentes (fazenda_id, nome, whatsapp_telefone) VALUES (1, 'G2', %s)", GERENTE_DEMO)
    chamadas = []

    def handler(request):
        corpo = json.loads(request.content)
        chamadas.append(corpo["to"])
        if corpo["to"] == str(GERENTE_EXEMPLO):
            return httpx.Response(500, json={"error": {"code": 2, "message": "fora do ar"}})
        return httpx.Response(200, json={})

    c = WhatsAppChannel(
        AsyncMock(),
        token="T",
        phone_number_id="1",
        cliente=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await c.pedir_aprovacao(incidente(1), 5, "x")  # um recebeu: não levanta
    assert sorted(chamadas) == sorted([str(GERENTE_EXEMPLO), str(GERENTE_DEMO)])


async def test_fazenda_sem_gerente_usa_o_padrao_e_sem_nenhum_falha_alto(meta):
    gerente(2, None)
    inc = incidente(3)
    await canal(meta, gerente_telefone=GERENTE_EXEMPLO).pedir_aprovacao(inc, 6, "x")
    assert [m["to"] for m in meta.enviados] == [str(GERENTE_EXEMPLO)]
    meta.enviados.clear()
    with pytest.raises(SemDestino):
        await canal(meta).pedir_aprovacao(inc, 6, "x")
    assert meta.enviados == []


async def test_operador_recebe_no_telefone_e_o_canal_diz_qual_coluna_usar(meta):
    assert canal(meta).campo_operador == "whatsapp_telefone"
    abrir_janela(OPERADOR)
    await canal(meta).notificar_operador(OPERADOR, "OS remarcada")
    assert meta.enviados[0]["to"] == str(OPERADOR) and meta.enviados[0]["text"]["body"] == "OS remarcada"


def test_achatar_junta_linhas_e_corta_no_limite():
    assert achatar("a\n\n b \n\tc") == "a · b · c"
    assert len(achatar("x" * 5000)) <= 1000 and achatar("x" * 5000).endswith("…")


# --- recebimento -----------------------------------------------------------------------------------
async def test_clique_em_botao_retoma_a_sessao_com_o_nome_de_quem_clicou_e_confirma(meta):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock()
    c = canal(meta, ao_responder)
    await c.processar(webhook(clique(f"r:{inc}:5:SIM")))
    ao_responder.assert_awaited_once_with(Resposta(inc, 5, "SIM", "Maria Gerente"))
    assert respostas_ao(meta, GERENTE_EXEMPLO) == ["Proposta aceita por Maria Gerente."]


async def test_clique_na_resposta_rapida_do_modelo_tambem_responde(meta):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock()
    await canal(meta, ao_responder).processar(webhook(clique_do_modelo(f"r:{inc}:5:NAO")))
    assert ao_responder.await_args.args[0].resposta == "NAO"


async def test_segundo_clique_responde_decisao_ja_registrada(meta):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock(side_effect=[None, DecisaoJaRegistrada("plano 5")])
    c = canal(meta, ao_responder)
    await c.processar(webhook(clique(f"r:{inc}:5:SIM", "wamid.a")))
    await c.processar(webhook(clique(f"r:{inc}:5:SIM", "wamid.b")))
    ultima = respostas_ao(meta, GERENTE_EXEMPLO)[-1]
    assert ultima == "Decisão já registrada para este plano. Nada foi alterado."
    assert ao_responder.await_count == 2


async def test_mesma_mensagem_reenviada_pela_meta_nao_decide_duas_vezes(meta):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock()
    c = canal(meta, ao_responder)
    carga = webhook(clique(f"r:{inc}:5:SIM", "wamid.igual"))
    await c.processar(carga)
    await c.processar(carga)
    assert ao_responder.await_count == 1


async def test_so_gerente_da_fazenda_do_incidente_aprova(meta):
    gerente(1, GERENTE_EXEMPLO)
    gerente(2, GERENTE_DEMO)
    inc = incidente(1)  # incidente da Fazenda Exemplo
    ao_responder = AsyncMock()
    c = canal(meta, ao_responder)
    # o gerente da Demo não decide incidente da Exemplo; um número qualquer, muito menos
    await c.processar(webhook(clique(f"r:{inc}:5:SIM", "wamid.1"), de=GERENTE_DEMO))
    await c.processar(webhook(clique(f"r:{inc}:5:SIM", "wamid.2"), de=OPERADOR))
    ao_responder.assert_not_awaited()
    assert "Só o gerente" in respostas_ao(meta, GERENTE_DEMO)[0]


async def test_falha_ao_registrar_avisa_sem_estourar(meta):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    c = canal(meta, AsyncMock(side_effect=RuntimeError("boom")))
    await c.processar(webhook(clique(f"r:{inc}:5:SIM")))
    assert "Tente pelo painel" in respostas_ao(meta, GERENTE_EXEMPLO)[0]


async def test_botao_que_nao_e_nosso_e_status_de_entrega_sao_ignorados(meta):
    ao_responder = AsyncMock()
    c = canal(meta, ao_responder)
    await c.processar(webhook(clique("outro:1")))
    await c.processar({"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]})
    ao_responder.assert_not_awaited()
    assert meta.enviados == []


async def test_receber_mensagem_abre_a_janela_e_a_proxima_aprovacao_vai_livre(meta):
    gerente(1, GERENTE_EXEMPLO)
    c = canal(meta)
    await c.pedir_aprovacao(incidente(1), 5, "x")
    assert meta.tipos() == ["template"]  # nunca falou com o número
    await c.processar(webhook(texto("oi")))
    meta.enviados.clear()
    await c.pedir_aprovacao(incidente(2), 6, "y")
    assert meta.tipos() == ["interactive"]


async def test_pergunta_do_gerente_roda_em_segundo_plano_e_responde(meta):
    gerente(1, GERENTE_EXEMPLO)
    c = canal(meta)
    resposta = "Abacus HC: 4 h sem chuva (Abacus HC, p. 7)"
    c.definir_ao_perguntar(AsyncMock(return_value=resposta))
    pergunta = "Posso aplicar Abacus HC com vento de 12 km/h?"
    await c.processar(webhook(texto(pergunta)))
    await c.aguardar()
    assert respostas_ao(meta, GERENTE_EXEMPLO) == ["Consultando as bulas…", resposta]
    c.ao_perguntar.assert_awaited_once_with(pergunta, "Maria Gerente")


async def test_numero_desconhecido_nao_gasta_llm(meta):
    c = canal(meta)
    c.definir_ao_perguntar(AsyncMock())
    await c.processar(webhook(texto("Qual a carência do Brucia?"), de=OPERADOR))
    c.ao_perguntar.assert_not_awaited()
    assert "não está liberado" in respostas_ao(meta, OPERADOR)[0]


async def test_numeros_extras_da_bula_podem_perguntar(meta):
    c = canal(meta, numeros_bula=f"{OPERADOR}, 5511900000000")
    c.definir_ao_perguntar(AsyncMock(return_value="ok"))
    await c.processar(webhook(texto("Qual a carência do Brucia?"), de=OPERADOR))
    await c.aguardar()
    c.ao_perguntar.assert_awaited_once()


async def test_falha_na_pergunta_avisa_sem_estourar(meta):
    gerente(1, GERENTE_EXEMPLO)
    c = canal(meta)
    c.definir_ao_perguntar(AsyncMock(side_effect=RuntimeError("boom")))
    await c.processar(webhook(texto("Qual a carência do Brucia?")))
    await c.aguardar()
    assert "Tente de novo" in respostas_ao(meta, GERENTE_EXEMPLO)[-1]


# --- webhook: assinatura e verificação --------------------------------------------------------------
def assinar(corpo: bytes, segredo: str = SEGREDO) -> str:
    return "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()


def test_assinatura_so_vale_com_o_segredo_certo_e_sem_segredo_nada_vale(meta):
    corpo = b'{"entry": []}'
    c = canal(meta)
    assert c.assinatura_valida(corpo, assinar(corpo))
    assert not c.assinatura_valida(corpo, assinar(corpo, "outro-segredo"))
    assert not c.assinatura_valida(corpo + b" ", assinar(corpo))  # corpo adulterado
    assert not c.assinatura_valida(corpo, None) and not c.assinatura_valida(corpo, "sha1=abc")
    assert not canal(meta, app_secret="").assinatura_valida(corpo, assinar(corpo, ""))


def test_verificacao_da_meta_devolve_o_desafio_so_com_o_token_certo(meta):
    c = canal(meta)
    assert c.verificar("subscribe", "verifica", "12345") == "12345"
    assert c.verificar("subscribe", "errado", "12345") is None
    assert c.verificar("unsubscribe", "verifica", "12345") is None
    assert canal(meta, verify_token="").verificar("subscribe", "", "12345") is None


def test_criar_canal_le_o_ambiente_e_sem_credenciais_manda_usar_o_painel(monkeypatch):
    monkeypatch.setenv("WHATSAPP_TOKEN", "T")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "99")
    monkeypatch.setenv("WHATSAPP_GERENTE_TELEFONE", str(GERENTE_EXEMPLO))
    c = criar_canal("whatsapp", AsyncMock())
    assert isinstance(c, WhatsAppChannel) and c.gerente_padrao == GERENTE_EXEMPLO
    assert c.url.endswith("/99/messages")
    monkeypatch.setenv("WHATSAPP_TOKEN", "")
    with pytest.raises(RuntimeError, match="CHANNEL=painel"):
        criar_canal("whatsapp", AsyncMock())


# --- rotas do `agents` -------------------------------------------------------------------------------
class OrquestradorFalso:
    def __init__(self, canal_):
        self.deps = SimpleNamespace(canal=canal_)

    async def aguardar(self):
        return None


def api(canal_):
    async def fabrica():
        return Servicos(orquestrador=OrquestradorFalso(canal_))

    return TestClient(criar_api(fabrica))


def verificacao(token: str) -> dict:
    return {"hub.mode": "subscribe", "hub.verify_token": token, "hub.challenge": "777"}


def test_rota_de_verificacao_devolve_o_desafio_ou_403(meta):
    with api(canal(meta)) as c:
        ok = c.get("/whatsapp/webhook", params=verificacao("verifica"))
        assert (ok.status_code, ok.text) == (200, "777")
        assert c.get("/whatsapp/webhook", params=verificacao("errado")).status_code == 403


def test_rota_do_webhook_recusa_sem_assinatura_e_aceita_com_ela(meta):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock()
    corpo = json.dumps(webhook(clique(f"r:{inc}:5:SIM"))).encode()
    falsa = {"X-Hub-Signature-256": assinar(corpo, "falso")}
    with api(canal(meta, ao_responder)) as c:
        assert c.post("/whatsapp/webhook", content=corpo).status_code == 403
        assert c.post("/whatsapp/webhook", content=corpo, headers=falsa).status_code == 403
        ao_responder.assert_not_awaited()
        ok = c.post("/whatsapp/webhook", content=corpo, headers={"X-Hub-Signature-256": assinar(corpo)})
        assert ok.status_code == 200  # responde logo; a decisão é processada em segundo plano
        for _ in range(100):
            if ao_responder.await_count:
                break
            time.sleep(0.05)
    assert ao_responder.await_count == 1


def test_rota_do_webhook_com_json_invalido_e_400(meta):
    corpo = b"isto nao e json"
    with api(canal(meta)) as c:
        r = c.post("/whatsapp/webhook", content=corpo, headers={"X-Hub-Signature-256": assinar(corpo)})
        assert r.status_code == 400


def test_sem_canal_whatsapp_as_rotas_nao_existem():
    with api(SimpleNamespace()) as c:
        assert c.get("/whatsapp/webhook").status_code == 404
        assert c.post("/whatsapp/webhook", content=b"{}").status_code == 404


def test_destinos_de_whatsapp_usam_a_coluna_propria():
    gerente(1, GERENTE_EXEMPLO)
    assert destinos.chats_dos_gerentes(1, destinos.CONTATO_WHATSAPP) == [GERENTE_EXEMPLO]
    assert destinos.chats_dos_gerentes(1) == []  # o chat do Telegram desse gerente está vazio
    with pytest.raises(ValueError):
        destinos.chats_dos_gerentes(1, "nome; DROP TABLE erp.gerentes")


async def test_executor_pega_o_contato_do_operador_pela_coluna_do_canal():
    from agents.nodes.executor import _chat_ids

    class Erp:
        async def listar_operadores(self, talhao_id=None):
            return [
                {"id": 1, "telegram_chat_id": 11, "whatsapp_telefone": None},
                {"id": 2, "telegram_chat_id": None, "whatsapp_telefone": OPERADOR},
            ]

    deps = SimpleNamespace(erp=Erp(), canal=SimpleNamespace(campo_operador="whatsapp_telefone"))
    assert await _chat_ids(deps, 1) == {2: OPERADOR}
    deps.canal = SimpleNamespace()  # canal sem o atributo (Telegram, fakes): o chat do Telegram
    assert await _chat_ids(deps, 1) == {1: 11}
