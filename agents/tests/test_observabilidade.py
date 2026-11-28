"""Observabilidade (Langfuse): estrutura dos traces, scores de negócio e o modo desligado.

Os spans vão para um exportador em memória (nada sai da máquina); os scores, que o SDK manda por
HTTP, são capturados no próprio cliente.
"""

import uuid

import pytest
from google.adk.sessions import InMemorySessionService
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agents import observabilidade as obs
from agents.config import Settings
from agents.runner import Orquestrador
from agents.tests.fakes import bula_que_cita, modelos_padrao, montar_cenario
from channels.base import Resposta


@pytest.fixture
def langfuse():
    """Cliente Langfuse real com exportador em memória e o ADK instrumentado só durante o teste."""
    from langfuse import Langfuse
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor

    exportador = InMemorySpanExporter()
    provedor = TracerProvider()
    cliente = Langfuse(
        # O SDK guarda um recurso por chave pública: uma chave por teste isola o exportador.
        public_key=f"pk-lf-teste-{uuid.uuid4().hex}",
        secret_key="sk-lf-teste",
        base_url="http://langfuse.invalido",
        tracer_provider=provedor,
        span_exporter=exportador,
        environment="teste",
    )
    scores: list[dict] = []
    cliente.create_score = lambda **kw: scores.append(kw)
    instrumentador = GoogleADKInstrumentor()
    instrumentador.instrument(tracer_provider=provedor)
    obs._cliente = cliente
    yield exportador, scores, provedor
    obs._cliente = None
    instrumentador.uninstrument()
    provedor.shutdown()


def spans(exportador, provedor):
    provedor.force_flush()
    return exportador.get_finished_spans()


def attr(span, chave):
    return (span.attributes or {}).get(chave)


def por_nome(scores):
    return {s["name"]: s for s in scores}


def test_sem_chaves_fica_desligado_e_tudo_e_no_op():
    assert obs.configurar(Settings(_env_file=None)) is False
    assert not obs.ativa()
    with obs.rastrear(obs.TRACE_PERGUNTAR_BULA, entrada="x") as rastro:
        assert not rastro.ativo
        rastro.saida("y")
        rastro.pontuar(obs.SCORE_BULA_ENCONTROU, True, "BOOLEAN")
    obs.pontuar(obs.SCORE_FALHA_AGENTE, "x", "CATEGORICAL")
    obs.sinalizar("ERROR", "x")
    obs.encerrar()


def test_desligado_pela_flag_mesmo_com_chaves():
    settings = Settings(
        _env_file=None, langfuse_public_key="pk", langfuse_secret_key="sk", langfuse_tracing_enabled=False
    )
    assert obs.configurar(settings) is False


def test_caso_de_uso_dentro_de_outro_vira_etapa_sem_renomear_o_trace(langfuse):
    """A bula consultada pelo assistente do chat fica dentro do trace `responder-chat`."""
    exportador, _, provedor = langfuse
    with obs.rastrear(obs.TRACE_RESPONDER_CHAT, sessao="chat-1", usuario="Maria"):
        with obs.rastrear(obs.TRACE_PERGUNTAR_BULA, usuario="assistente") as interno:
            assert interno.ativo
    todos = spans(exportador, provedor)
    chat = next(s for s in todos if s.name == obs.TRACE_RESPONDER_CHAT)
    bula = next(s for s in todos if s.name == obs.TRACE_PERGUNTAR_BULA)
    assert bula.parent.span_id == chat.context.span_id
    assert {attr(s, "langfuse.trace.name") for s in todos} == {obs.TRACE_RESPONDER_CHAT}
    assert {attr(s, "user.id") for s in todos} == {"Maria"}


def test_excecao_no_bloco_marca_o_trace_como_erro_e_sobe(langfuse):
    exportador, _, provedor = langfuse
    with pytest.raises(RuntimeError), obs.rastrear(obs.TRACE_EXTRAIR_LIMITES):
        raise RuntimeError("quebrou")
    [raiz] = [s for s in spans(exportador, provedor) if s.name == obs.TRACE_EXTRAIR_LIMITES]
    assert attr(raiz, "langfuse.observation.level") == "ERROR"


async def test_incidente_vira_trace_com_sessao_agentes_e_scores(langfuse):
    exportador, scores, provedor = langfuse
    c = montar_cenario()
    orq = Orquestrador(c.deps, InMemorySessionService())
    assert await orq.iniciar(7) is True

    todos = spans(exportador, provedor)
    [raiz] = [s for s in todos if s.name == obs.TRACE_AVALIAR_INCIDENTE]
    assert raiz.parent is None
    assert attr(raiz, "session.id") == "incidente-7"
    assert attr(raiz, "langfuse.trace.name") == obs.TRACE_AVALIAR_INCIDENTE
    assert set(attr(raiz, "langfuse.trace.tags")) >= {"agents", "incidente"}
    assert '"aguardando_aprovacao"' in attr(raiz, "langfuse.observation.output")

    # Tudo o que o ADK fez está no mesmo trace, com a sessão propagada.
    do_trace = [s for s in todos if s.context.trace_id == raiz.context.trace_id]
    assert len(do_trace) == len(todos)
    assert all(attr(s, "session.id") == "incidente-7" for s in do_trace)
    nomes = " ".join(s.name for s in do_trace)
    for agente in ("avaliador_risco", "agronomico", "operacional", "supervisor"):
        assert agente in nomes
    assert any(attr(s, "openinference.span.kind") == "LLM" for s in do_trace)
    assert any(attr(s, "openinference.span.kind") == "TOOL" for s in do_trace)

    trace_id = format(raiz.context.trace_id, "032x")
    assert all(s["trace_id"] == trace_id for s in scores)
    s = por_nome(scores)
    assert s[obs.SCORE_AVALIACAO_RISCO]["value"] == "violacao"
    assert s[obs.SCORE_VIOLACOES_DESCARTADAS]["value"] == 0.0
    assert s[obs.SCORE_PLANO_VALIDADO]["value"] == 1.0
    assert s[obs.SCORE_DESFECHO_INCIDENTE]["value"] == "aguardando_aprovacao"

    # A resposta do gerente abre outro trace na mesma sessão, com a decisão como score.
    exportador.clear()
    scores.clear()
    assert await orq.responder(Resposta(7, 1, "SIM", "gerente"), esperar=True) == "ok"
    [retomada] = [s for s in spans(exportador, provedor) if s.name == obs.TRACE_RETOMAR_INCIDENTE]
    assert attr(retomada, "session.id") == "incidente-7"
    assert attr(retomada, "user.id") == "gerente"
    s = por_nome(scores)
    assert s[obs.SCORE_DECISAO_GERENTE]["value"] == "SIM"
    assert s[obs.SCORE_DESFECHO_INCIDENTE]["value"] == "encerrado"


async def test_falha_do_agente_vira_erro_no_span_e_score(langfuse):
    exportador, scores, provedor = langfuse
    c = montar_cenario(modelos_padrao(avaliador=[RuntimeError("LLM fora do ar")]))
    orq = Orquestrador(c.deps, InMemorySessionService())
    await orq.iniciar(7)

    s = por_nome(scores)
    assert s[obs.SCORE_FALHA_AGENTE]["value"] == "avaliador_risco"
    assert "LLM fora do ar" in s[obs.SCORE_FALHA_AGENTE]["comment"]
    assert s[obs.SCORE_AVALIACAO_RISCO]["value"] == "falhou"
    assert s[obs.SCORE_DESFECHO_INCIDENTE]["value"] == "aguardando_decisao_manual"
    marcados = [s for s in spans(exportador, provedor) if attr(s, "langfuse.observation.level") == "ERROR"]
    assert any("LLM fora do ar" in (attr(s, "langfuse.observation.status_message") or "") for s in marcados)


async def test_pergunta_a_bula_vira_trace_com_scores_de_citacao(langfuse):
    from agents.bula import PergunteBula

    exportador, scores, provedor = langfuse
    # A página 12 não veio da busca: a citação é descartada em código e o score registra isso.
    c = montar_cenario(modelos_padrao(bula=[bula_que_cita(pagina=12)]))
    resultado = await PergunteBula(c.deps).perguntar("Qual o limite de chuva?", "Abacus HC", quem="painel")

    [raiz] = [s for s in spans(exportador, provedor) if s.name == obs.TRACE_PERGUNTAR_BULA]
    assert attr(raiz, "user.id") == "painel"
    assert "Qual o limite de chuva?" in attr(raiz, "langfuse.observation.input")
    assert any(attr(s, "openinference.span.kind") == "LLM" for s in spans(exportador, provedor))
    s = por_nome(scores)
    assert not resultado.encontrou
    assert s[obs.SCORE_BULA_ENCONTROU]["value"] == 0.0
    assert s[obs.SCORE_CITACOES_DESCARTADAS]["value"] == 1.0
