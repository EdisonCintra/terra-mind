"""Avaliador de Risco: decide, pelos trechos das bulas, se a previsão viola o uso de cada OS."""

from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext

from agents import ferramentas as f
from agents.agentes._comum import REGRAS_GERAIS, criar_agente, j, os_resumidas, previsao, raiz_para_adk
from agents.deps import Dependencias
from agents.modelos import AvaliacaoRisco


def instrucao(rc: ReadonlyContext) -> str:
    estado = rc.state
    return f"""{REGRAS_GERAIS}

Você é o Avaliador de Risco. Para CADA ordem de serviço abaixo, decida se a previsão do tempo viola as
condições de uso do produto aplicado, segundo o documento técnico (bula do Agrofit ou referência da Embrapa).

Como trabalhar:
1. Para cada OS, chame buscar_trechos com o `produto` da OS (para bula, secao="condicoes_climaticas"; para
   referência de fertilizante, sem seção). Faça consultas como "chuva após a aplicação",
   "intervalo sem chuva",
   "velocidade do vento".
2. Ache no trecho o limite que a previsão ultrapassa no período da aplicação (início previsto + duração e,
   para chuva, as horas seguintes que o documento indicar).
3. Só marque viola=true se o trecho trouxer o limite. Copie em `trecho` o texto literal do documento e use
   `fonte_doc` e `pagina` exatamente como vieram da ferramenta. Converta unidades para as do sistema.
4. Se nenhum trecho trouxer o limite, viola=false e explique na justificativa.

Campos de cada item: os_id, viola, parametro ("chuva"|"vento"|"umidade"|"temperatura"), limite, unidade,
janela_h (horas após o início em que o limite vale; ex.: "não chover 24 h após" → 24), valor_previsto,
trecho, fonte_doc, pagina, justificativa. Devolva um item por OS.

Ordens de serviço em risco:
{j(os_resumidas(estado))}

Gatilhos do monitor:
{j(estado["incidente"]["gatilhos"])}

Previsão em blocos de 6 h:
{j(previsao(estado))}"""


def criar(deps: Dependencias) -> Agent:
    return criar_agente(
        deps,
        "avaliador",
        name="avaliador_risco",
        description="Decide, pelos trechos das bulas, se a previsão viola o uso de cada OS.",
        instruction=instrucao,
        tools=[f.criar_buscar_trechos(deps)],
        output_schema=AvaliacaoRisco,
        saida_vazia='{"violacoes": []}',
    )


def __getattr__(nome: str):
    if nome == "root_agent":
        return raiz_para_adk(criar)
    raise AttributeError(nome)
