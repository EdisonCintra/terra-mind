from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from google.adk.models.base_llm import BaseLlm

from agents.canal import Canal
from agents.clientes.conhecimento import ConhecimentoCliente
from agents.clientes.erp import ErpCliente
from agents.clientes.repositorio import RepositorioAgente
from agents.config import NomeAgente, Settings
from agents.rules.janelas import FUSO


def agora_sp() -> datetime:
    return datetime.now(FUSO)


def criar_modelo(settings: Settings, agente: NomeAgente) -> BaseLlm:
    """`LiteLlm` da OpenAI no formato `openai/<modelo>` (decisão A3).

    A chave vai explícita porque o `Settings` também lê o `.env`, que o LiteLLM não enxerga.
    Timeout e novas tentativas ficam no cliente: o agente roda como nó do grafo, e o que ainda
    falhar vira a marca de falha das proteções do agente.

    `output_schema_and_tools=False`: com `response_format` e ferramentas na mesma requisição, o
    gpt-4o-mini repete as chamadas de ferramenta e nunca dá a resposta final (o Avaliador ficou em
    loop). Assim o ADK manda só as ferramentas e acrescenta `set_model_response` para a saída no schema.
    """
    from google.adk.models._capabilities import LlmCapabilities
    from google.adk.models.lite_llm import LiteLlm

    class LiteLlmSemSchemaComFerramentas(LiteLlm):
        @property
        def capabilities(self) -> LlmCapabilities:
            return LlmCapabilities(**super().capabilities.model_dump() | {"output_schema_and_tools": False})

    extra = {"api_key": settings.openai_api_key} if settings.openai_api_key else {}
    if settings.reasoning_effort:
        extra["reasoning_effort"] = settings.reasoning_effort
    return LiteLlmSemSchemaComFerramentas(
        model=settings.modelo(agente),
        timeout=settings.timeout_llm_s,
        num_retries=settings.tentativas_llm,
        **extra,
    )


@dataclass
class Dependencias:
    """Tudo o que os nós usam. Nos testes, cada campo recebe um fake."""

    settings: Settings
    erp: ErpCliente
    conhecimento: ConhecimentoCliente
    repo: RepositorioAgente
    canal: Canal
    modelos: dict[NomeAgente, BaseLlm | str] = field(default_factory=dict)
    relogio: Callable[[], datetime] = agora_sp

    def modelo(self, agente: NomeAgente) -> BaseLlm | str:
        if agente in self.modelos:
            return self.modelos[agente]
        return criar_modelo(self.settings, agente)
