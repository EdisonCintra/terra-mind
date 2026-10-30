"""Supervisor: concilia as duas análises num plano com uma opção por OS suspensa."""

from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext

from agents import ferramentas as f
from agents.agentes._comum import REGRAS_GERAIS, criar_agente, j, os_resumidas, raiz_para_adk
from agents.deps import Dependencias
from agents.modelos import PlanoProposto


def instrucao(rc: ReadonlyContext) -> str:
    estado = rc.state
    retorno = ""
    if estado.get("motivo_reprovacao"):
        retorno += f"\nO Validador REPROVOU a proposta anterior: {estado['motivo_reprovacao']}\nCorrija.\n"
    if estado.get("pedido_gerente"):
        retorno += f"\nO gerente pediu outra opção. {estado['pedido_gerente']}\n"
    return f"""{REGRAS_GERAIS}

Você é o Supervisor. Concilie as análises do Agronômico e do Operacional num plano de reagendamento com
UMA opção por OS suspensa. Para cada OS, chame janelas_validas e escolha uma janela e uma máquina dessa
lista (use exatamente o `inicio` devolvido). Use calcular_impacto se quiser comparar o custo.
Escreva `mensagem` para o gerente no Telegram: no máximo 5 linhas, direta, dizendo o que foi suspenso, por
quê e a proposta. Não escreva valores em R$ nem páginas: o sistema acrescenta esses dados conferidos.
{retorno}
OS suspensas: {j(estado.get("os_suspensas"))}
Ordens de serviço: {j(os_resumidas(estado))}
Análise agronômica: {estado.get("analise_agronomica")}
Análise operacional: {estado.get("analise_operacional")}"""


def criar(deps: Dependencias) -> Agent:
    return criar_agente(
        deps,
        "supervisor",
        name="supervisor",
        description="Concilia as análises e propõe o plano de reagendamento ao gerente.",
        instruction=instrucao,
        tools=[f.criar_janelas_validas(deps), f.criar_calcular_impacto(deps)],
        output_schema=PlanoProposto,
        saida_vazia='{"opcoes": [], "mensagem": ""}',
    )


def __getattr__(nome: str):
    if nome == "root_agent":
        return raiz_para_adk(criar)
    raise AttributeError(nome)
