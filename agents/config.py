from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVICO = Path(__file__).resolve().parent  # agents/ (o pacote é a pasta do serviço, D3)

NomeAgente = Literal["avaliador", "agronomico", "operacional", "supervisor", "bula"]


class Settings(BaseSettings):
    """Configuração do serviço `agents`, lida das variáveis de ambiente (contrato 13)."""

    # `.env` da raiz do monorepo e o do serviço, pelo local deste arquivo (vale de qualquer pasta).
    model_config = SettingsConfigDict(
        env_file=(_SERVICO.parent / ".env", _SERVICO / ".env"), extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql://terramind:terramind@localhost:5432/terramind"
    erp_mcp_url: str = "http://localhost:8030/mcp"
    conhecimento_url: str = "http://localhost:8010"

    # Modelos da OpenAI pelo LiteLLM, no formato `openai/<modelo>`.
    openai_api_key: str | None = None
    model_avaliador: str = "openai/gpt-6-luna"
    model_agronomico: str = "openai/gpt-6-luna"
    model_operacional: str = "openai/gpt-6-luna"
    model_supervisor: str = "openai/gpt-6-luna"
    model_bula: str = "openai/gpt-6-luna"  # G4: Pergunte à bula
    # Esforço de raciocínio dos quatro agentes. O gpt-6-luna não aceita "minimal": o menor é "low"
    # ("none" desliga o raciocínio). Vazio = o padrão do modelo (para modelos sem raciocínio).
    reasoning_effort: str | None = "low"

    channel: Literal["telegram", "painel"] = "painel"

    approval_timeout_min: int = 30
    intervalo_timeout_s: int = 60
    max_replanejamentos: int = 2
    max_voltas_validador: int = 2
    timeout_llm_s: float = 90
    tentativas_llm: int = 2
    janela_padrao_h: int = 48

    # G5: fração do valor da aplicação perdida por dia de atraso, enquanto o conhecimento não tiver
    # `condicao="atraso"` (entrega do E7). Sempre mostrada como estimativa.
    fator_atraso_dia: float = 0.02
    fonte_fator_atraso: str = (
        "estimativa: provisória até o E7; atraso reduz a eficácia do programa de aplicação, e as bulas"
        " indexadas não quantificam a perda por dia (o Ativum, p. 4, fixa intervalo máximo de 14 dias)"
    )

    def modelo(self, agente: NomeAgente) -> str:
        return getattr(self, f"model_{agente}")
