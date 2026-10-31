"""Fazenda simulada para o `adk web` / `adk run`.

LLMs: os modelos `MODEL_*` da OpenAI se `OPENAI_API_KEY` estiver definida (no ambiente ou no
`.env`); senão, ou com `DEMO_LLM=fake`, LLMs roteirizados que reproduzem o cenário.
"""

import functools
import logging
import os
from datetime import datetime

from google.adk import Agent
from google.adk.apps import App

from agents.config import Settings
from agents.modelos import AvaliacaoRisco, Impacto, como_json
from agents.nodes.contexto import para_os_em_risco
from agents.rules.impacto import calcular_perda
from agents.rules.janelas import FUSO
from agents.simulacao import (
    Cenario,
    FakeCanal,
    FakeRepo,
    avaliacao_violando,
    incidente_chuva,
    montar_cenario,
    os_da_fazenda,
)
from agents.workflow import criar_app

logger = logging.getLogger("terramind.demo")

INCIDENTE_DEMO = 1


class CanalDoTerminal(FakeCanal):
    """Guarda as mensagens e as mostra no terminal do `adk web`."""

    async def enviar_alerta(self, texto):
        await super().enviar_alerta(texto)
        logger.warning("[alerta ao gerente] %s", texto)

    async def pedir_aprovacao(self, incidente_id, plano_id, texto):
        await super().pedir_aprovacao(incidente_id, plano_id, texto)
        logger.warning("[aprovação pedida] incidente %s, plano %s\n%s", incidente_id, plano_id, texto)

    async def notificar_operador(self, chat_id, texto):
        await super().notificar_operador(chat_id, texto)
        logger.warning("[operador %s] %s", chat_id, texto)


class RepoDaDemo(FakeRepo):
    """Incidente desconhecido → cria o cenário de chuva e recoloca a fazenda no estado inicial."""

    def __init__(self, agora: datetime, reiniciar_fazenda):
        super().__init__()
        self.agora = agora
        self.reiniciar_fazenda = reiniciar_fazenda

    async def carregar_incidente(self, incidente_id):
        if incidente_id not in self.incidentes:
            self.incidentes[incidente_id] = incidente_chuva(incidente_id, self.agora)
            self.reiniciar_fazenda()
        return await super().carregar_incidente(incidente_id)


def _montar() -> Cenario:
    settings = Settings()
    llm_real = bool(settings.openai_api_key) and os.environ.get("DEMO_LLM", "").lower() != "fake"
    agora = datetime.now(FUSO).replace(minute=0, second=0, microsecond=0)
    cenario = montar_cenario(modelos={} if llm_real else None, settings=settings, agora=agora)

    def reiniciar_fazenda():
        cenario.erp.os = os_da_fazenda(agora)
        cenario.erp.estoque = {1: 500.0}
        for modelo in cenario.deps.modelos.values():
            modelo.chamadas = 0

    repo = RepoDaDemo(agora, reiniciar_fazenda)
    cenario.erp.planos_aprovados = repo.aprovado
    cenario.repo = cenario.deps.repo = repo
    cenario.canal = cenario.deps.canal = CanalDoTerminal()
    logger.warning("TerraMind demo: LLMs %s", "da OpenAI" if llm_real else "roteirizados (DEMO_LLM=fake)")
    return cenario


@functools.cache
def app_terramind() -> App:
    return criar_app(_montar().deps, id_da_entrada=True)


# ---------------------------------------------------------------- agentes sozinhos


def estado_do_cenario(cenario: Cenario) -> dict:
    """O estado que o workflow teria montado até o Supervisor, no cenário de chuva."""
    agora = cenario.deps.relogio()
    incidente = incidente_chuva(INCIDENTE_DEMO, agora)
    talhao = cenario.erp.talhoes[1]
    os_risco = para_os_em_risco(cenario.erp._linha(cenario.erp.os[1]), talhao)
    fator = cenario.conhecimento.fatores[(os_risco.tipo_insumo, "chuva")]
    impacto = Impacto(
        os_id=1,
        area_ha=os_risco.area_ha,
        dose_por_ha=os_risco.dose_por_ha,
        preco_unitario=os_risco.preco_unitario,
        fator=fator["fator"],
        fonte_fator=fator["fonte_ou_estimativa"],
        perda_rs=calcular_perda(
            os_risco.area_ha, os_risco.dose_por_ha, os_risco.preco_unitario, fator["fator"]
        ),
    )
    return {
        "incidente_id": INCIDENTE_DEMO,
        "incidente": como_json(incidente),
        "os_em_risco": como_json([os_risco]),
        "avaliacao": como_json(AvaliacaoRisco.model_validate(avaliacao_violando())),
        "os_suspensas": [1],
        "impactos": [como_json(impacto)],
        "analise_agronomica": "Milho em V6: chuva após a aplicação lava o fungicida (bula-abacus-hc, p. 7).",
        "analise_operacional": "Pulverizador A livre depois da chuva, a partir das 06:00.",
    }


@functools.cache
def _cenario_dos_agentes() -> Cenario:
    return _montar()


def agente_sozinho(criar) -> Agent:
    """O agente da pasta como agente raiz, com o estado do cenário de chuva preenchido."""
    cenario = _cenario_dos_agentes()
    agente = criar(cenario.deps)
    modelo = agente.model

    async def preencher_estado(callback_context):
        if hasattr(modelo, "chamadas"):  # LLM roteirizado: recomeça o roteiro a cada conversa
            modelo.chamadas = 0
        if "os_em_risco" not in callback_context.state:
            for chave, valor in estado_do_cenario(cenario).items():
                callback_context.state[chave] = valor

    agente.before_agent_callback = preencher_estado
    return agente
