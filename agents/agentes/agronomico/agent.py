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

Como buscar (no máximo 4 buscas; a cultura e o estágio já estão nas ordens de serviço, use
consultar_talhao só se faltarem):
1. Fenologia: buscar_trechos SEM `produto`, com `cultura` igual à cultura do talhão e uma consulta pelo nome
   do estágio, descrevendo-o. Ex.: consulta="estádio V6 do milho seis folhas desenvolvidas",
   cultura="milho". Com `produto` os documentos de fenologia não aparecem, e uma pergunta genérica sobre
   risco não traz o trecho do estágio.
2. Bula: buscar_trechos com o `produto` da OS, por exemplo "<cultura>: número, época e intervalo de
   aplicação" e "intervalo de segurança".
Relacione o estágio (o que a planta está definindo, a sensibilidade a chuva) com o que a bula diz e com a
janela de remarcação. Se um documento não tratar do assunto, diga isso em uma frase, sem especular.

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
