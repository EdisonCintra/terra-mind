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
    """
    from google.adk.models.lite_llm import LiteLlm

    extra = {"api_key": settings.openai_api_key} if settings.openai_api_key else {}
    return LiteLlm(
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
