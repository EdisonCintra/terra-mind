"""O grafo do incidente (decisão A1). Ver o fluxograma 1 do plano do Gustavo.

Os quatro agentes (`avaliador_risco`, `agronomico`, `operacional`, `supervisor`) são nós do grafo.
Cada um é seguido de um nó em código que lê a saída e roteia; uma falha do LLM não derruba o
workflow, porque as proteções do agente (`agents.agentes._comum.protecoes`) a transformam numa marca
no estado. Todo roteador tem `DEFAULT_ROUTE` para a decisão manual: uma rota inesperada nunca deixa
o incidente parado no meio.
"""

from google.adk import Workflow
from google.adk.apps import App, ResumabilityConfig
from google.adk.workflow import DEFAULT_ROUTE, JoinNode

from agents.agentes.agronomico.agent import criar as criar_agronomico
from agents.agentes.avaliador_risco.agent import criar as criar_avaliador
from agents.agentes.operacional.agent import criar as criar_operacional
from agents.agentes.supervisor.agent import criar as criar_supervisor
from agents.deps import Dependencias
from agents.nodes.acao_preventiva import criar_acao_preventiva
from agents.nodes.aprovacao import criar_aprovacao, criar_rota_resposta
from agents.nodes.avaliador import criar_rota_avaliacao
from agents.nodes.contexto import criar_contexto
from agents.nodes.desfechos import criar_decisao_manual, criar_encerrar
from agents.nodes.executor import criar_executor
from agents.nodes.planejar import criar_preparar_plano, criar_validador

APP_NAME = "terramind"


def criar_workflow(deps: Dependencias, id_da_entrada: bool = False) -> Workflow:
    contexto = criar_contexto(deps, id_da_entrada)
    avaliador = criar_avaliador(deps)
    rota_avaliacao = criar_rota_avaliacao(deps)
    acao_preventiva = criar_acao_preventiva(deps)
    agronomico = criar_agronomico(deps)
    operacional = criar_operacional(deps)
    analises = JoinNode(name="analises")
    preparar_plano = criar_preparar_plano(deps)
    supervisor = criar_supervisor(deps)
    validador = criar_validador(deps)
    aprovacao = criar_aprovacao(deps)
    rota_resposta = criar_rota_resposta(deps)
    executor = criar_executor(deps)
    decisao_manual = criar_decisao_manual(deps)
    encerrar = criar_encerrar(deps)

    return Workflow(
        name="incidente",
        edges=[
            ("START", contexto),
            (contexto, {"com_os": avaliador, "sem_os": encerrar, DEFAULT_ROUTE: decisao_manual}),
            (avaliador, rota_avaliacao),
            (
                rota_avaliacao,
                {"violacao": acao_preventiva, "sem_violacao": encerrar, DEFAULT_ROUTE: decisao_manual},
            ),
            (acao_preventiva, {"suspensas": agronomico, DEFAULT_ROUTE: decisao_manual}),
            (acao_preventiva, {"suspensas": operacional}),
            (agronomico, analises),
            (operacional, analises),
            (analises, preparar_plano, supervisor, validador),
            (validador, {"valido": aprovacao, "revisar": supervisor, DEFAULT_ROUTE: decisao_manual}),
            (aprovacao, rota_resposta),
            (rota_resposta, {"SIM": executor, "OPCOES": preparar_plano, DEFAULT_ROUTE: decisao_manual}),
            (executor, {"ok": encerrar, "revalidar": preparar_plano, DEFAULT_ROUTE: decisao_manual}),
        ],
    )


def criar_app(deps: Dependencias, id_da_entrada: bool = False) -> App:
    """`id_da_entrada=True` só na demonstração: o id do incidente vem do texto digitado."""
    return App(
        name=APP_NAME,
        root_agent=criar_workflow(deps, id_da_entrada),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )
