"""Agente Operacional: propõe como remarcar as OS suspensas (janela, máquina, conflitos)."""

from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext

from agents import ferramentas as f
from agents.agentes._comum import REGRAS_GERAIS, criar_agente, j, os_resumidas, raiz_para_adk
from agents.deps import Dependencias


def instrucao(rc: ReadonlyContext) -> str:
    estado = rc.state
    return f"""{REGRAS_GERAIS}

Você é o Agente Operacional. As OS abaixo foram suspensas. Para cada uma, chame janelas_validas e proponha
como remarcar: qual janela, qual máquina (prefira a já alocada), e se outras OS do talhão ou da máquina
conflitam (use listar_os e disponibilidade_maquinas). Responda em até 8 frases, com datas e ids.

OS suspensas: {j(estado.get("os_suspensas"))}
Ordens de serviço: {j(os_resumidas(estado))}"""


def criar(deps: Dependencias) -> Agent:
    return criar_agente(
        deps,
        "operacional",
        name="operacional",
        description="Propõe como reorganizar máquinas e janelas para as OS suspensas.",
        instruction=instrucao,
        tools=[
            f.criar_janelas_validas(deps),
            f.criar_listar_os(deps),
            f.criar_disponibilidade_maquinas(deps),
            f.criar_listar_operadores(deps),
        ],
        output_key="analise_operacional",
        # Falha vira texto: o Supervisor segue com o que tiver e o Validador continua barrando.
        saida_vazia="(análise operacional indisponível)",
    )


def __getattr__(nome: str):
    if nome == "root_agent":
        return raiz_para_adk(criar)
    raise AttributeError(nome)
