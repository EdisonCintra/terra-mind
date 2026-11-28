"""Observabilidade dos agentes no Langfuse, sobre OpenTelemetry.

Quatro camadas, da mais automática à mais de negócio:

1. **Instrumentação do ADK** (`GoogleADKInstrumentor`, OpenInference): um span por invocação, por nó do
   workflow e por agente; uma `generation` por chamada de LLM (modelo, tokens, custo, raciocínio) e um
   `tool` por ferramenta, aninhados como o ADK executou. Os tracers nativos do ADK ficam desligados
   pelo próprio instrumentador, então não há span duplicado.
2. **Um trace por caso de uso** (`rastrear`): nome estável (`TRACE_*`), entrada e saída legíveis,
   sessão, usuário, tags e metadados. No incidente, a sessão do Langfuse é a mesma sessão do ADK
   (`incidente-<id>`): a avaliação e cada retomada pelo gerente aparecem em sequência na mesma sessão.
3. **Scores de negócio** (`pontuar`, `Rastro.pontuar`): o que o código confere depois do LLM (citação
   inventada, violação contraditória, plano reprovado, decisão do gerente, desfecho do incidente).
   São as métricas de qualidade dos agentes nos dashboards e nos avaliadores do Langfuse.
4. **Sinais das proteções** (`sinalizar`): falhas que as proteções dos agentes transformam em saída
   vazia (para não derrubar o workflow) ficam marcadas como WARNING/ERROR no span em que aconteceram.

Sem `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`, ou com `LANGFUSE_TRACING_ENABLED=false`, tudo vira
no-op: testes, `adk web` e ambientes sem Langfuse não exportam nada e não mudam de comportamento.
Nenhuma função daqui levanta exceção para quem chama: observabilidade nunca derruba o fluxo.

Os nomes de trace e de score são contrato com os dashboards e avaliadores do Langfuse: não mude um
nome sem migrar o que o referencia.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from langfuse import Langfuse

    from agents.config import Settings

logger = logging.getLogger(__name__)

SERVICO = "agents"

# Traces (um por caso de uso). Verbo primeiro, sem valores dinâmicos (eles vão para os metadados).
TRACE_AVALIAR_INCIDENTE = "avaliar-incidente"  # do START até a pausa da aprovação ou o fim
TRACE_RETOMAR_INCIDENTE = "retomar-incidente"  # resposta do gerente → executor/novo plano/fim
TRACE_PERGUNTAR_BULA = "perguntar-bula"  # G4
TRACE_EXTRAIR_LIMITES = "extrair-limites"  # contrato 26
TRACE_RESPONDER_CHAT = "responder-chat"  # assistente do chat nos canais (uma pergunta do gerente)

# Scores.
SCORE_DESFECHO_INCIDENTE = "desfecho-incidente"  # CATEGORICAL: status do incidente ao fim da execução
SCORE_DECISAO_GERENTE = "decisao-gerente"  # CATEGORICAL: SIM | NAO | OPCOES (feedback humano)
SCORE_AVALIACAO_RISCO = "avaliacao-risco"  # CATEGORICAL: violacao | sem_violacao | falhou
SCORE_VIOLACOES_DESCARTADAS = "violacoes-descartadas"  # NUMERIC: violações que os números contradizem
SCORE_PLANO_VALIDADO = "plano-validado"  # BOOLEAN: o Validador aprovou a proposta do Supervisor
SCORE_BULA_ENCONTROU = "bula-encontrou"  # BOOLEAN: a resposta saiu com citação conferida
SCORE_CITACOES_DESCARTADAS = "citacoes-descartadas"  # NUMERIC: citações que não vieram da busca
SCORE_LIMITES_ACEITOS = "limites-aceitos"  # NUMERIC
SCORE_LIMITES_DESCARTADOS = "limites-descartados"  # NUMERIC: limites reprovados na conferência
SCORE_FALHA_AGENTE = "falha-agente"  # CATEGORICAL: nome do agente cuja saída virou a saída vazia
# CATEGORICAL: permitida | bloqueada_entrada | bloqueada_saida | muito_longa | erro (NeMo Guardrails no chat)
SCORE_GUARDRAIL = "guardrail"

TipoScore = Literal["NUMERIC", "CATEGORICAL", "BOOLEAN"]
Nivel = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]

_LIMITE_METADADO = 200  # o Langfuse descarta metadados propagados acima de 200 caracteres

_cliente: Langfuse | None = None


def ativa() -> bool:
    return _cliente is not None


def configurar(settings: Settings) -> bool:
    """Liga o Langfuse e instrumenta o ADK. Idempotente; devolve se o rastreamento ficou ativo."""
    global _cliente
    if _cliente is not None:
        return True
    if not settings.langfuse_tracing_enabled:
        logger.info("observabilidade: desligada (LANGFUSE_TRACING_ENABLED=false)")
        return False
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.info("observabilidade: sem chaves do Langfuse; rastreamento desligado")
        return False
    try:
        from langfuse import Langfuse
        from openinference.instrumentation.google_adk import GoogleADKInstrumentor

        # As chaves vão explícitas: o `Settings` lê o `.env`, que o SDK do Langfuse não enxerga.
        cliente = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            base_url=settings.langfuse_base_url,
            environment=settings.langfuse_tracing_environment,
            release=settings.langfuse_release,
            sample_rate=settings.langfuse_sample_rate,
            tracing_enabled=True,
        )
        instrumentador = GoogleADKInstrumentor()
        if not instrumentador.is_instrumented_by_opentelemetry:
            instrumentador.instrument()
    except Exception:  # noqa: BLE001 - sem Langfuse o serviço segue igual
        logger.exception("observabilidade: falha ao configurar o Langfuse; rastreamento desligado")
        return False
    _cliente = cliente
    logger.info(
        "observabilidade: Langfuse ativo em %s (ambiente %s)",
        settings.langfuse_base_url,
        settings.langfuse_tracing_environment,
    )
    return True


def encerrar() -> None:
    """Exporta o que estiver no buffer e fecha o cliente (desligamento do serviço ou fim de script)."""
    global _cliente
    if _cliente is None:
        return
    try:
        _cliente.shutdown()
    except Exception:  # noqa: BLE001
        logger.exception("observabilidade: falha ao exportar os últimos spans")
    finally:
        _cliente = None


class Rastro:
    """O trace raiz de um caso de uso. Sem Langfuse, todos os métodos são no-op."""

    def __init__(self, observacao: Any = None, trace_id: str | None = None):
        self._obs = observacao
        self.trace_id = trace_id

    @property
    def ativo(self) -> bool:
        return self._obs is not None

    def saida(self, valor: Any, **metadados: Any) -> None:
        if self._obs is None:
            return
        try:
            self._obs.update(output=valor, metadata=metadados or None)
        except Exception:  # noqa: BLE001
            logger.debug("observabilidade: falha ao gravar a saída", exc_info=True)

    def sinalizar(self, nivel: Nivel, mensagem: str) -> None:
        if self._obs is None:
            return
        try:
            self._obs.update(level=nivel, status_message=mensagem[:1000])
        except Exception:  # noqa: BLE001
            logger.debug("observabilidade: falha ao sinalizar o trace", exc_info=True)

    def pontuar(
        self, nome: str, valor: float | str | bool, tipo: TipoScore, comentario: str | None = None
    ) -> None:
        _criar_score(nome, valor, tipo, comentario, trace_id=self.trace_id)


@contextlib.contextmanager
def rastrear(
    nome: str,
    *,
    entrada: Any = None,
    sessao: str | None = None,
    usuario: str | None = None,
    tags: Sequence[str] = (),
    metadados: dict[str, Any] | None = None,
) -> Iterator[Rastro]:
    """Abre o trace raiz de um caso de uso; tudo o que o ADK fizer dentro dele fica aninhado.

    `sessao`, `usuario`, `tags` e `metadados` são propagados a todos os spans filhos, então os filtros
    do Langfuse acham também as `generation` e os `tool` do trace. Uma exceção que escapa do bloco
    marca o trace como ERROR e segue subindo.

    Chamado dentro de outro trace (ex.: a bula consultada pelo assistente do chat), vira uma etapa dele:
    não renomeia o trace nem troca a sessão e o usuário de quem o abriu.
    """
    if _cliente is None:
        yield Rastro()
        return
    try:
        from langfuse import propagate_attributes

        aninhado = _cliente.get_current_trace_id() is not None
        pilha = contextlib.ExitStack()
        obs = pilha.enter_context(
            _cliente.start_as_current_observation(
                as_type="chain", name=nome, input=entrada, metadata=metadados or None
            )
        )
        if not aninhado:
            pilha.enter_context(
                propagate_attributes(
                    trace_name=nome,
                    session_id=sessao,
                    user_id=usuario,
                    tags=sorted({SERVICO, *tags}),
                    metadata=_metadados_propagados(metadados),
                )
            )
        rastro = Rastro(obs, getattr(obs, "trace_id", None))
    except Exception:  # noqa: BLE001
        logger.exception("observabilidade: falha ao abrir o trace %s", nome)
        yield Rastro()
        return
    with pilha:
        try:
            yield rastro
        except BaseException as e:
            rastro.sinalizar("ERROR", f"{type(e).__name__}: {e}")
            raise


@contextlib.contextmanager
def etapa(
    nome: str,
    *,
    tipo: Literal["span", "guardrail", "tool", "evaluator"] = "span",
    entrada: Any = None,
) -> Iterator[Rastro]:
    """Uma etapa dentro do trace em execução (ex.: a verificação do guardrail), com o tipo do Langfuse."""
    if _cliente is None:
        yield Rastro()
        return
    try:
        pilha = contextlib.ExitStack()
        obs = pilha.enter_context(
            _cliente.start_as_current_observation(as_type=tipo, name=nome, input=entrada)
        )
        rastro = Rastro(obs, getattr(obs, "trace_id", None))
    except Exception:  # noqa: BLE001
        logger.debug("observabilidade: falha ao abrir a etapa %s", nome, exc_info=True)
        yield Rastro()
        return
    with pilha:
        yield rastro


def pontuar(nome: str, valor: float | str | bool, tipo: TipoScore, comentario: str | None = None) -> None:
    """Score no trace em execução (para os nós do workflow, que não têm o `Rastro` em mãos)."""
    if _cliente is None:
        return
    try:
        trace_id = _cliente.get_current_trace_id()
    except Exception:  # noqa: BLE001
        trace_id = None
    if trace_id:
        _criar_score(nome, valor, tipo, comentario, trace_id=trace_id)


def sinalizar(nivel: Nivel, mensagem: str, **metadados: Any) -> None:
    """Marca o span em execução (LLM, ferramenta ou agente) com WARNING/ERROR e o motivo."""
    if _cliente is None:
        return
    try:
        _cliente.update_current_span(level=nivel, status_message=mensagem[:1000], metadata=metadados or None)
    except Exception:  # noqa: BLE001
        logger.debug("observabilidade: falha ao sinalizar o span", exc_info=True)


def _criar_score(
    nome: str,
    valor: float | str | bool,
    tipo: TipoScore,
    comentario: str | None,
    *,
    trace_id: str | None,
) -> None:
    if _cliente is None or not trace_id:
        return
    if tipo == "BOOLEAN":
        valor = 1.0 if valor else 0.0
    elif tipo == "NUMERIC":
        valor = float(valor)
    else:
        valor = str(valor)
    try:
        _cliente.create_score(name=nome, value=valor, data_type=tipo, comment=comentario, trace_id=trace_id)
    except Exception:  # noqa: BLE001
        logger.debug("observabilidade: falha ao gravar o score %s", nome, exc_info=True)


def _metadados_propagados(metadados: dict[str, Any] | None) -> dict[str, str] | None:
    """O `propagate_attributes` só aceita texto curto: números e ids viram str, o resto é cortado."""
    if not metadados:
        return None
    resultado = {}
    for chave, valor in metadados.items():
        if valor is None:
            continue
        texto = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False, default=str)
        resultado[chave] = texto[:_LIMITE_METADADO]
    return resultado or None
