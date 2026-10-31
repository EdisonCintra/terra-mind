"""Agente Agronômico: interpreta o risco para a cultura e o estágio, citando fonte e página."""

from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext

from agents import ferramentas as f
from agents.agentes._comum import REGRAS_GERAIS, criar_agente, j, os_resumidas, raiz_para_adk
from agents.deps import Dependencias


def instrucao(rc: ReadonlyContext) -> str:
    estado = rc.state
    return f"""{REGRAS_GERAIS}

Você é o Agente Agronômico. As OS abaixo foram suspensas porque a previsão viola o documento técnico.
Explique, em até 6 frases, o risco agronômico para a cultura e o estágio de cada talhão (perda de produto,
lixiviação, deriva, janela de aplicação) e o que considerar ao remarcar. Cite fonte e página de cada
afirmação técnica.

Como buscar: a base de conhecimento tem só as bulas dos produtos (não há documentos de fenologia). Chame
buscar_trechos com o `produto` da OS e consultas com o nome da cultura, por exemplo
"<cultura>: número, época e intervalo de aplicação" e "intervalo de segurança". Relacione o que a bula diz
com o estágio do talhão (já está nas ordens de serviço; use consultar_talhao só se faltar). Se a bula não
falar do estágio, diga isso em uma frase, sem especular. No máximo 3 buscas.

OS suspensas: {j(estado.get("os_suspensas"))}
Ordens de serviço: {j(os_resumidas(estado))}
Avaliação de risco: {j(estado.get("avaliacao"))}"""


def criar(deps: Dependencias) -> Agent:
    return criar_agente(
        deps,
        "agronomico",
        name="agronomico",
        description="Interpreta o risco para a cultura e o estágio e cita a fonte.",
        instruction=instrucao,
        tools=[f.criar_buscar_trechos(deps), f.criar_consultar_talhao(deps)],
        output_key="analise_agronomica",
        # Falha vira texto: o Supervisor segue com o que tiver e o Validador continua barrando.
        saida_vazia="(análise agronômica indisponível)",
    )


def __getattr__(nome: str):
    if nome == "root_agent":
        return raiz_para_adk(criar)
    raise AttributeError(nome)
