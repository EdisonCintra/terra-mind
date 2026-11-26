"""WhatsApp pelo Twilio (F4): envio com janela de 24 h, botões, webhook assinado e destino por fazenda.

O Twilio é simulado com `httpx.MockTransport`; o banco é o Postgres real dos outros testes.
Seed: fazenda 1 tem os talhões 1 e 2; a Fazenda Demo (2) tem o talhão 3.
"""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlencode

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from agents.api import Servicos, criar_api
from channels import destinos
from channels.base import DecisaoJaRegistrada, Resposta, criar_canal
from channels.destinos import SemDestino
from channels.whatsapp import (
    COMO_RESPONDER,
    ERROS_FORA_DA_JANELA,
    WhatsAppChannel,
    WhatsAppErro,
    achatar,
    assinatura_twilio,
    decisao_do_texto,
)
from tests.conftest import DATABASE_URL

GERENTE_EXEMPLO = 5564999990001
GERENTE_DEMO = 5564999990002
OPERADOR = 5564999990003
TOKEN = "token-do-twilio"
SID_CONTA = "AC00000000000000000000000000000000"
ERRO_JANELA = next(iter(ERROS_FORA_DA_JANELA))
URL_WEBHOOK = "https://exemplo.ngrok.app/whatsapp/webhook"


def _sql(sql: str, *params):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        linha = conn.execute(sql, params)
        return linha.fetchone() if linha.description else None


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    for chave in ("WHATSAPP_GERENTE_TELEFONE", "WHATSAPP_NUMEROS_BULA", "TWILIO_WEBHOOK_URL"):
        monkeypatch.delenv(chave, raising=False)


class Twilio:
    """A API de mensagens de mentira: guarda o que o canal mandou e pode recusar mensagem livre por janela."""

    def __init__(self):
        self.enviados: list[dict] = []
        self.recusar_livre_com: int | None = None
        self.contador = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/2010-04-01/Accounts/{SID_CONTA}/Messages.json"
        assert request.headers["Authorization"].startswith("Basic ")
        corpo = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert corpo["From"] == "whatsapp:+14155238886"
        if self.recusar_livre_com and "ContentSid" not in corpo:
            return httpx.Response(400, json={"code": self.recusar_livre_com, "message": "janela"})
        self.contador += 1
        corpo["sid_devolvido"] = f"SM{self.contador:032d}"
        self.enviados.append(corpo)
        return httpx.Response(201, json={"sid": corpo["sid_devolvido"]})

    def para(self, telefone: int) -> list[dict]:
        return [m for m in self.enviados if m["To"] == f"whatsapp:+{telefone}"]

    def textos(self, telefone: int) -> list[str]:
        return [m["Body"] for m in self.para(telefone) if "Body" in m]

    def modelos(self) -> list[dict]:
        return [m for m in self.enviados if "ContentSid" in m]


@pytest.fixture
def twilio():
    return Twilio()


def canal(twilio, ao_responder=None, **kw) -> WhatsAppChannel:
    return WhatsAppChannel(
        ao_responder or AsyncMock(),
        account_sid=SID_CONTA,
        auth_token=TOKEN,
        de="whatsapp:+14155238886",
        cliente=httpx.AsyncClient(transport=httpx.MockTransport(twilio)),
        **{"content_aprovacao": "", "content_aviso": "", "webhook_url": "", **kw},
    )


def com_modelos(twilio, ao_responder=None, **kw) -> WhatsAppChannel:
    return canal(twilio, ao_responder, content_aprovacao="HXaprovacao", content_aviso="HXaviso", **kw)


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


def plano(incidente_id: int) -> int:
    return _sql(
        "INSERT INTO agente.planos (incidente_id, opcoes_json) VALUES (%s, '{}'::jsonb) RETURNING id",
        incidente_id,
    )[0]


def abrir_janela(telefone: int, ha_horas: float = 0) -> None:
    _sql(
        "INSERT INTO agente.whatsapp_janelas (telefone, ultima_mensagem_em)"
        " VALUES (%s, now() - make_interval(secs => %s))"
        " ON CONFLICT (telefone) DO UPDATE SET ultima_mensagem_em = EXCLUDED.ultima_mensagem_em",
        telefone,
        ha_horas * 3600,
    )


# --- mensagens recebidas (campos do webhook do Twilio) -------------------------------------------------
def recebida(
    de: int = GERENTE_EXEMPLO, nome: str = "Maria Gerente", sid: str = "SMrecebida1", **campos
) -> dict:
    return {"From": f"whatsapp:+{de}", "ProfileName": nome, "MessageSid": sid, "NumMedia": "0", **campos}


def clique(botao: str, sid: str = "SMclique1", **campos) -> dict:
    return recebida(sid=sid, ButtonPayload=botao, ButtonText="x", Body="x", **campos)


def texto(corpo: str, sid: str = "SMtexto1", **campos) -> dict:
    return recebida(sid=sid, Body=corpo, **campos)


# --- envio -----------------------------------------------------------------------------------------
async def test_na_janela_sem_modelo_a_aprovacao_vai_como_texto_pedindo_sim_opcoes_ou_nao(twilio):
    gerente(1, GERENTE_EXEMPLO)
    gerente(2, GERENTE_DEMO)
    abrir_janela(GERENTE_DEMO)
    inc = incidente(3)  # Talhão Demo
    await canal(twilio).pedir_aprovacao(inc, 6, "Plano da Fazenda Demo")
    (msg,) = twilio.enviados
    assert msg["To"] == f"whatsapp:+{GERENTE_DEMO}"
    assert msg["Body"] == f"Plano da Fazenda Demo\n\n{COMO_RESPONDER}"
    assert _sql(
        "SELECT incidente_id, plano_id FROM agente.whatsapp_mensagens WHERE sid = %s", msg["sid_devolvido"]
    ) == (inc, 6)


async def test_na_janela_com_modelo_a_aprovacao_vai_com_os_tres_botoes(twilio):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO)
    inc = incidente(1)
    await com_modelos(twilio).pedir_aprovacao(inc, 5, "Plano:\nremarcar a OS 1")
    (msg,) = twilio.enviados
    assert msg["ContentSid"] == "HXaprovacao" and "Body" not in msg
    assert json.loads(msg["ContentVariables"]) == {
        "1": "Plano:\nremarcar a OS 1"
    }  # na janela, o texto vai inteiro
    assert _sql("SELECT plano_id FROM agente.whatsapp_mensagens WHERE sid = %s", msg["sid_devolvido"]) == (5,)


async def test_fora_da_janela_a_aprovacao_vai_como_modelo_com_o_texto_achatado(twilio):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO, ha_horas=30)  # última mensagem há 30 h: fechada
    inc = incidente(1)
    await com_modelos(twilio).pedir_aprovacao(inc, 5, "Plano:\nremarcar a OS 1\n\n\nObrigado")
    (msg,) = twilio.enviados
    assert msg["ContentSid"] == "HXaprovacao"
    assert json.loads(msg["ContentVariables"]) == {
        "1": "Plano: · remarcar a OS 1 · Obrigado"
    }  # sem quebra de linha
    assert _sql("SELECT plano_id FROM agente.whatsapp_mensagens WHERE sid = %s", msg["sid_devolvido"]) == (5,)


async def test_contato_novo_sem_janela_recebe_modelo(twilio):
    gerente(1, GERENTE_EXEMPLO)
    await com_modelos(twilio).pedir_aprovacao(incidente(1), 5, "x")
    assert [m["ContentSid"] for m in twilio.enviados] == ["HXaprovacao"]


async def test_fora_da_janela_sem_modelo_falha_alto_explicando(twilio):
    gerente(1, GERENTE_EXEMPLO)
    with pytest.raises(WhatsAppErro, match="fora da janela de 24 h"):
        await canal(twilio).pedir_aprovacao(incidente(1), 5, "x")
    assert twilio.enviados == []


async def test_se_o_twilio_recusa_a_mensagem_livre_por_janela_cai_para_o_modelo(twilio):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO)  # o banco acha que está aberta, o Twilio discorda
    twilio.recusar_livre_com = ERRO_JANELA
    await com_modelos(twilio).enviar_alerta("falha", incidente_id=incidente(1))
    assert [m["ContentSid"] for m in twilio.enviados] == ["HXaviso"]


async def test_outro_erro_do_twilio_nao_vira_modelo_e_sobe(twilio):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO)
    twilio.recusar_livre_com = 20003  # autenticação
    with pytest.raises(WhatsAppErro, match="recusou o envio"):
        await com_modelos(twilio).enviar_alerta("falha", incidente_id=incidente(1))
    assert twilio.enviados == []


async def test_corpo_longo_vai_como_texto_e_os_botoes_com_corpo_curto(twilio):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO)
    longo = "Plano detalhado. " * 100  # > 1024
    await com_modelos(twilio).pedir_aprovacao(incidente(1), 5, longo)
    assert [("Body" in m, "ContentSid" in m) for m in twilio.enviados] == [(True, False), (False, True)]
    assert len(json.loads(twilio.enviados[1]["ContentVariables"])["1"]) < 1024


async def test_texto_livre_acima_do_limite_do_twilio_e_cortado(twilio):
    gerente(1, GERENTE_EXEMPLO)
    abrir_janela(GERENTE_EXEMPLO)
    await canal(twilio).enviar_alerta("x" * 5000, incidente_id=incidente(1))
    assert len(twilio.enviados[0]["Body"]) == 1600 and twilio.enviados[0]["Body"].endswith("…")


async def test_alerta_do_incidente_vai_ao_gerente_da_fazenda_dentro_e_fora_da_janela(twilio):
    gerente(1, GERENTE_EXEMPLO)
    gerente(2, GERENTE_DEMO)
    abrir_janela(GERENTE_DEMO)
    c = com_modelos(twilio)
    await c.enviar_alerta("falha na Demo", incidente_id=incidente(3))
    await c.enviar_alerta(f"Incidente {incidente(1)}: OS 1 segue SUSPENSA")  # sem id: acha pelo texto
    assert [(m["To"], "Body" in m) for m in twilio.enviados] == [
        (f"whatsapp:+{GERENTE_DEMO}", True),
        (f"whatsapp:+{GERENTE_EXEMPLO}", False),
    ]
    assert twilio.enviados[1]["ContentSid"] == "HXaviso"


async def test_todos_os_gerentes_da_fazenda_recebem_e_um_que_falha_nao_impede_os_outros():
    gerente(1, GERENTE_EXEMPLO)
    _sql("INSERT INTO erp.gerentes (fazenda_id, nome, whatsapp_telefone) VALUES (1, 'G2', %s)", GERENTE_DEMO)
    chamadas = []

    def handler(request):
        corpo = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        chamadas.append(corpo["To"])
        if corpo["To"] == f"whatsapp:+{GERENTE_EXEMPLO}":
            return httpx.Response(500, json={"code": 2, "message": "fora do ar"})
        return httpx.Response(201, json={"sid": "SM1"})

    c = WhatsAppChannel(
        AsyncMock(),
        account_sid=SID_CONTA,
        auth_token=TOKEN,
        de="+14155238886",
        content_aprovacao="",
        cliente=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    abrir_janela(GERENTE_EXEMPLO)
    abrir_janela(GERENTE_DEMO)
    await c.pedir_aprovacao(incidente(1), 5, "x")  # um recebeu: não levanta
    assert sorted(chamadas) == sorted([f"whatsapp:+{GERENTE_EXEMPLO}", f"whatsapp:+{GERENTE_DEMO}"])


async def test_fazenda_sem_gerente_usa_o_padrao_e_sem_nenhum_falha_alto(twilio):
    gerente(2, None)
    abrir_janela(GERENTE_EXEMPLO)
    inc = incidente(3)
    await canal(twilio, gerente_telefone=GERENTE_EXEMPLO).pedir_aprovacao(inc, 6, "x")
    assert [m["To"] for m in twilio.enviados] == [f"whatsapp:+{GERENTE_EXEMPLO}"]
    twilio.enviados.clear()
    with pytest.raises(SemDestino):
        await canal(twilio).pedir_aprovacao(inc, 6, "x")
    assert twilio.enviados == []


async def test_operador_recebe_no_telefone_e_o_canal_diz_qual_coluna_usar(twilio):
    assert canal(twilio).campo_operador == "whatsapp_telefone"
    abrir_janela(OPERADOR)
    await canal(twilio).notificar_operador(OPERADOR, "OS remarcada")
    assert (
        twilio.enviados[0]["To"] == f"whatsapp:+{OPERADOR}" and twilio.enviados[0]["Body"] == "OS remarcada"
    )


def test_achatar_junta_linhas_e_corta_no_limite():
    assert achatar("a\n\n b \n\tc") == "a · b · c"
    assert len(achatar("x" * 5000)) <= 1000 and achatar("x" * 5000).endswith("…")


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("SIM", "SIM"),
        (" *Sim!* ", "SIM"),
        ("Aceitar Proposta", "SIM"),
        ("não", "NAO"),
        ("NAO", "NAO"),
        ("Recusar Proposta", "NAO"),
        ("Opções", "OPCOES"),
        ("Ver próxima opção", "OPCOES"),
        ("sim, mas e a bula?", None),
        ("Posso aplicar Abacus HC?", None),
        ("", None),
    ],
)
def test_decisao_do_texto(texto, esperado):
    assert decisao_do_texto(texto) == esperado


# --- recebimento -----------------------------------------------------------------------------------
async def aprovacao_enviada(twilio, c, inc, plano_id, telefone=GERENTE_EXEMPLO) -> str:
    abrir_janela(telefone)
    await c.pedir_aprovacao(inc, plano_id, "Plano")
    return twilio.para(telefone)[-1]["sid_devolvido"]


async def test_clique_no_botao_acha_o_plano_pela_mensagem_respondida_e_confirma(twilio):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    p = plano(inc)
    ao_responder = AsyncMock()
    c = com_modelos(twilio, ao_responder)
    sid = await aprovacao_enviada(twilio, c, inc, p)
    await c.processar(clique("SIM", OriginalRepliedMessageSid=sid))
    ao_responder.assert_awaited_once_with(Resposta(inc, p, "SIM", "Maria Gerente"))
    assert twilio.textos(GERENTE_EXEMPLO)[-1] == "Proposta aceita por Maria Gerente."


async def test_clique_sem_a_mensagem_respondida_vale_para_o_plano_mais_recente_sem_resposta(twilio):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    antigo, novo = plano(inc), plano(inc)
    _sql("UPDATE agente.planos SET respondido_em = now() WHERE id = %s", antigo)
    ao_responder = AsyncMock()
    c = com_modelos(twilio, ao_responder)
    await aprovacao_enviada(twilio, c, inc, antigo)
    await aprovacao_enviada(twilio, c, inc, novo)
    await c.processar(clique("OPCOES"))
    assert ao_responder.await_args.args[0] == Resposta(inc, novo, "OPCOES", "Maria Gerente")


async def test_clique_com_o_plano_no_identificador_tambem_responde(twilio):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock()
    await canal(twilio, ao_responder).processar(clique(f"r:{inc}:5:NAO"))
    assert ao_responder.await_args.args[0].resposta == "NAO" and ao_responder.await_args.args[0].plano_id == 5


async def test_gerente_escreve_sim_ou_nao_e_vale_como_clique(twilio):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    p = plano(inc)
    ao_responder = AsyncMock()
    c = canal(twilio, ao_responder)
    await aprovacao_enviada(twilio, c, inc, p)
    await c.processar(texto("não", "SMa"))
    ao_responder.assert_awaited_once_with(Resposta(inc, p, "NAO", "Maria Gerente"))
    assert twilio.textos(GERENTE_EXEMPLO)[-1] == "Proposta recusada por Maria Gerente."


async def test_sim_sem_plano_esperando_nao_decide_nada(twilio):
    gerente(1, GERENTE_EXEMPLO)
    ao_responder = AsyncMock()
    c = canal(twilio, ao_responder)
    await c.processar(texto("sim"))
    ao_responder.assert_not_awaited()


async def test_botao_sem_plano_conhecido_avisa_para_usar_o_painel(twilio):
    gerente(1, GERENTE_EXEMPLO)
    ao_responder = AsyncMock()
    await canal(twilio, ao_responder).processar(clique("SIM"))
    ao_responder.assert_not_awaited()
    assert "Responda pelo painel" in twilio.textos(GERENTE_EXEMPLO)[0]


async def test_segundo_clique_responde_decisao_ja_registrada(twilio):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock(side_effect=[None, DecisaoJaRegistrada("plano 5")])
    c = canal(twilio, ao_responder)
    await c.processar(clique(f"r:{inc}:5:SIM", "SMa"))
    await c.processar(clique(f"r:{inc}:5:SIM", "SMb"))
    assert twilio.textos(GERENTE_EXEMPLO)[-1] == "Decisão já registrada para este plano. Nada foi alterado."
    assert ao_responder.await_count == 2


async def test_mesma_mensagem_reenviada_pelo_twilio_nao_decide_duas_vezes(twilio):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock()
    c = canal(twilio, ao_responder)
    carga = clique(f"r:{inc}:5:SIM", "SMigual")
    await c.processar(carga)
    await c.processar(carga)
    assert ao_responder.await_count == 1


async def test_so_gerente_da_fazenda_do_incidente_aprova(twilio):
    gerente(1, GERENTE_EXEMPLO)
    gerente(2, GERENTE_DEMO)
    inc = incidente(1)  # incidente da Fazenda Exemplo
    ao_responder = AsyncMock()
    c = canal(twilio, ao_responder)
    # o gerente da Demo não decide incidente da Exemplo; um número qualquer, muito menos
    await c.processar(clique(f"r:{inc}:5:SIM", "SM1", de=GERENTE_DEMO))
    await c.processar(clique(f"r:{inc}:5:SIM", "SM2", de=OPERADOR))
    ao_responder.assert_not_awaited()
    assert "Só o gerente" in twilio.textos(GERENTE_DEMO)[0]


async def test_falha_ao_registrar_avisa_sem_estourar(twilio):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    c = canal(twilio, AsyncMock(side_effect=RuntimeError("boom")))
    await c.processar(clique(f"r:{inc}:5:SIM"))
    assert "Tente pelo painel" in twilio.textos(GERENTE_EXEMPLO)[0]


async def test_mensagem_sem_remetente_e_ignorada(twilio):
    ao_responder = AsyncMock()
    await canal(twilio, ao_responder).processar({"MessageSid": "SMx", "Body": "oi"})
    ao_responder.assert_not_awaited()
    assert twilio.enviados == []


async def test_receber_mensagem_abre_a_janela_e_a_proxima_aprovacao_vai_livre(twilio):
    gerente(1, GERENTE_EXEMPLO)
    c = com_modelos(twilio)
    await c.pedir_aprovacao(incidente(1), 5, "x")
    assert [m["ContentSid"] for m in twilio.enviados] == ["HXaprovacao"]  # nunca falou com o número
    await c.processar(texto("oi"))  # "oi" de um gerente vira pergunta à bula sem a bula ligada
    twilio.enviados.clear()
    await c.pedir_aprovacao(incidente(2), 6, "y")
    assert [("Body" in m) for m in twilio.enviados] == [False]  # modelo de botões, mas pela janela aberta
    assert twilio.enviados[0]["ContentSid"] == "HXaprovacao"
    assert _janela_registrada(GERENTE_EXEMPLO)


def _janela_registrada(telefone: int) -> bool:
    return _sql("SELECT 1 FROM agente.whatsapp_janelas WHERE telefone = %s", telefone) is not None


async def test_pergunta_do_gerente_roda_em_segundo_plano_e_responde(twilio):
    gerente(1, GERENTE_EXEMPLO)
    c = canal(twilio)
    resposta = "Abacus HC: 4 h sem chuva (Abacus HC, p. 7)"
    c.definir_ao_perguntar(AsyncMock(return_value=resposta))
    pergunta = "Posso aplicar Abacus HC com vento de 12 km/h?"
    await c.processar(texto(pergunta))
    await c.aguardar()
    assert twilio.textos(GERENTE_EXEMPLO) == ["Consultando as bulas…", resposta]
    c.ao_perguntar.assert_awaited_once_with(pergunta, "Maria Gerente")


async def test_numero_desconhecido_nao_gasta_llm(twilio):
    c = canal(twilio)
    c.definir_ao_perguntar(AsyncMock())
    await c.processar(texto("Qual a carência do Brucia?", de=OPERADOR))
    c.ao_perguntar.assert_not_awaited()
    assert "não está liberado" in twilio.textos(OPERADOR)[0]


async def test_numeros_extras_da_bula_podem_perguntar(twilio):
    c = canal(twilio, numeros_bula=f"{OPERADOR}, 5511900000000")
    c.definir_ao_perguntar(AsyncMock(return_value="ok"))
    await c.processar(texto("Qual a carência do Brucia?", de=OPERADOR))
    await c.aguardar()
    c.ao_perguntar.assert_awaited_once()


async def test_falha_na_pergunta_avisa_sem_estourar(twilio):
    gerente(1, GERENTE_EXEMPLO)
    c = canal(twilio)
    c.definir_ao_perguntar(AsyncMock(side_effect=RuntimeError("boom")))
    await c.processar(texto("Qual a carência do Brucia?"))
    await c.aguardar()
    assert "Tente de novo" in twilio.textos(GERENTE_EXEMPLO)[-1]


# --- webhook: assinatura -----------------------------------------------------------------------------
def test_assinatura_e_hmac_sha1_da_url_mais_nome_e_valor_dos_parametros_em_ordem():
    """Regra do `RequestValidator` do Twilio: URL + nome e valor dos parâmetros → HMAC-SHA1 → base64."""
    import base64
    import hashlib
    import hmac

    parametros = [("To", "+18005551212"), ("CallSid", "CA1234567890ABCDE"), ("Digits", "1234")]
    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    texto = url + "CallSidCA1234567890ABCDEDigits1234To+18005551212"
    esperada = base64.b64encode(
        hmac.new(b"12345678901234567890", texto.encode(), hashlib.sha1).digest()
    ).decode()
    assert assinatura_twilio("12345678901234567890", url, parametros) == esperada
    assert assinatura_twilio("12345678901234567890", url, list(reversed(parametros))) == esperada


def test_assinatura_so_vale_com_url_parametros_e_token_certos(twilio):
    c = canal(twilio)
    parametros = [("Body", "sim"), ("From", "whatsapp:+5564999990001")]
    boa = assinatura_twilio(TOKEN, URL_WEBHOOK, parametros)
    assert c.assinatura_valida(URL_WEBHOOK, parametros, boa)
    assert not c.assinatura_valida(
        URL_WEBHOOK, parametros, assinatura_twilio("outro", URL_WEBHOOK, parametros)
    )
    assert not c.assinatura_valida(URL_WEBHOOK + "x", parametros, boa)  # outra URL
    assert not c.assinatura_valida(URL_WEBHOOK, [*parametros, ("Extra", "1")], boa)  # corpo adulterado
    assert not c.assinatura_valida(URL_WEBHOOK, parametros, None) and not c.assinatura_valida(
        URL_WEBHOOK, parametros, ""
    )


def test_criar_canal_le_o_ambiente_e_sem_credenciais_manda_usar_o_painel(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", SID_CONTA)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "+1 (415) 523-8886")
    monkeypatch.setenv("WHATSAPP_GERENTE_TELEFONE", str(GERENTE_EXEMPLO))
    c = criar_canal("whatsapp", AsyncMock())
    assert isinstance(c, WhatsAppChannel) and c.gerente_padrao == GERENTE_EXEMPLO
    assert c.de == "whatsapp:+14155238886" and c.url.endswith(f"/Accounts/{SID_CONTA}/Messages.json")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "")
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


def formulario(campos: dict, url: str = URL_WEBHOOK, token: str = TOKEN) -> tuple[bytes, dict]:
    assinatura = assinatura_twilio(token, url, list(campos.items()))
    return urlencode(campos).encode(), {
        "X-Twilio-Signature": assinatura,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "exemplo.ngrok.app",
    }


def esperar(ao_responder):
    for _ in range(100):
        if ao_responder.await_count:
            return
        time.sleep(0.05)


def test_rota_recusa_sem_assinatura_ou_com_assinatura_falsa_e_aceita_com_ela(twilio):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock()
    campos = clique(f"r:{inc}:5:SIM")
    corpo, cabecalhos = formulario(campos)
    sem_assinatura = {k: v for k, v in cabecalhos.items() if k != "X-Twilio-Signature"}
    falsa = {
        **cabecalhos,
        "X-Twilio-Signature": assinatura_twilio("falso", URL_WEBHOOK, list(campos.items())),
    }
    with api(canal(twilio, ao_responder)) as c:
        assert c.post("/whatsapp/webhook", content=corpo, headers=sem_assinatura).status_code == 403
        assert c.post("/whatsapp/webhook", content=corpo, headers=falsa).status_code == 403
        ao_responder.assert_not_awaited()
        ok = c.post("/whatsapp/webhook", content=corpo, headers=cabecalhos)
        assert ok.status_code == 200 and ok.text == "<Response/>" and "xml" in ok.headers["content-type"]
        esperar(ao_responder)  # responde logo; a decisão é processada em segundo plano
    assert ao_responder.await_count == 1


def test_a_url_assinada_pode_vir_da_variavel_quando_o_proxy_nao_informa(twilio):
    gerente(1, GERENTE_EXEMPLO)
    inc = incidente(1)
    ao_responder = AsyncMock()
    campos = clique(f"r:{inc}:5:SIM")
    corpo, cabecalhos = formulario(campos, url="https://dominio-fixo.exemplo/whatsapp/webhook")
    cabecalhos.pop("X-Forwarded-Proto")
    cabecalhos.pop("X-Forwarded-Host")
    with api(canal(twilio, ao_responder, webhook_url="https://dominio-fixo.exemplo/whatsapp/webhook")) as c:
        assert c.post("/whatsapp/webhook", content=corpo, headers=cabecalhos).status_code == 200
        esperar(ao_responder)
    assert ao_responder.await_count == 1


def test_a_rota_antiga_da_meta_nao_existe_mais(twilio):
    with api(canal(twilio)) as c:
        assert c.get("/whatsapp/webhook", params={"hub.mode": "subscribe"}).status_code == 405


def test_sem_canal_whatsapp_a_rota_nao_existe():
    with api(SimpleNamespace()) as c:
        assert c.post("/whatsapp/webhook", content=b"From=x").status_code == 404


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


# --- modelos (Content API) -----------------------------------------------------------------------------
def test_modelos_do_twilio_tem_botoes_fixos_e_corpo_com_a_variavel():
    from channels import twilio_modelos as tm

    aprovacao = tm.modelo_aprovacao()
    quick = aprovacao["types"]["twilio/quick-reply"]
    assert quick["body"] == "{{1}}"
    assert [(a["id"], a["title"]) for a in quick["actions"]] == [
        ("SIM", "Aceitar Proposta"),
        ("OPCOES", "Ver próxima opção"),
        ("NAO", "Recusar Proposta"),
    ]
    assert all(len(a["title"]) <= 20 for a in quick["actions"])
    assert tm.modelo_aviso()["types"]["twilio/text"]["body"] == "{{1}}"


def test_criar_e_aprovar_modelos_falam_com_a_content_api():
    from channels import twilio_modelos as tm

    chamadas = []

    def handler(request):
        chamadas.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={"sid": f"HX{len(chamadas)}"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert tm.criar(http, ("AC1", "t")) == {
            "TWILIO_CONTENT_SID_APROVACAO": "HX1",
            "TWILIO_CONTENT_SID_AVISO": "HX2",
        }
        tm.aprovar(http, ("AC1", "t"), "HX1", "terramind_aprovacao")
    assert [c[0] for c in chamadas] == [
        "/v1/Content",
        "/v1/Content",
        "/v1/Content/HX1/ApprovalRequests/whatsapp",
    ]
    assert chamadas[2][1] == {"name": "terramind_aprovacao", "category": "UTILITY"}
