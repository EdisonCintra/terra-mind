"""Cada agente tem a sua pasta no padrão do ADK e carrega pelo mesmo loader do `adk web`."""

from pathlib import Path

import pytest
from google.adk.apps import App
from google.adk.cli.utils.agent_loader import AgentLoader

PASTA = Path(__file__).resolve().parents[1] / "agentes"
AGENTES = ["agronomico", "avaliador_risco", "operacional", "supervisor"]


@pytest.fixture
def loader(monkeypatch):
    monkeypatch.setenv("DEMO_LLM", "fake")
    return AgentLoader(str(PASTA))


def test_adk_web_lista_uma_pasta_por_agente(loader):
    assert loader.list_agents() == [*AGENTES, "terramind"]


@pytest.mark.parametrize("nome", AGENTES)
def test_agente_sozinho_e_raiz_sem_mode(loader, nome):
    agente = loader.load_agent(nome)
    assert agente.name == nome and agente.mode is None and agente.before_agent_callback


def test_terramind_e_o_workflow_com_retomada(loader):
    app = loader.load_agent("terramind")
    assert isinstance(app, App) and app.root_agent.name == "incidente"
    assert app.resumability_config.is_resumable


def test_no_grafo_os_agentes_rodam_em_single_turn():
    """Sem `mode` no agente, o ADK usa `single_turn` para um nó de grafo (doc de workflows)."""
    from google.adk.workflow._llm_agent_wrapper import _effective_llm_agent_mode

    from agents.agentes.avaliador_risco.agent import criar
    from agents.tests.fakes import montar_cenario

    agente = criar(montar_cenario().deps)
    assert agente.mode is None and _effective_llm_agent_mode(agente) == "single_turn"
