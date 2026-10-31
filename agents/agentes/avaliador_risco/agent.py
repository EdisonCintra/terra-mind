"""Avaliador de Risco: decide, pelos trechos das bulas, se a previsão viola o uso de cada OS."""

from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext

from agents import ferramentas as f
from agents.agentes._comum import (
    REGRAS_GERAIS,
    criar_agente,
    j,
    os_resumidas,
    previsao_por_os,
    raiz_para_adk,
)
from agents.deps import Dependencias
from agents.modelos import AvaliacaoRisco


def instrucao(rc: ReadonlyContext) -> str:
    estado = rc.state
    return f"""{REGRAS_GERAIS}

Você é o Avaliador de Risco. Para CADA ordem de serviço abaixo, decida se a previsão do tempo viola as
condições de uso do defensivo aplicado, segundo a bula do produto.

Como trabalhar:
1. Para cada OS, chame buscar_trechos com o `produto` da OS e SEM `secao` (cada bula põe o limite de
   chuva numa seção diferente). Faça consultas como "chuva após a aplicação", "umidade relativa",
   "temperatura", "velocidade do vento".
2. Compare cada limite do trecho com a "Previsão no horário de cada OS" (já calculada em código):
   - vento, umidade e temperatura: só os valores de `aplicacao` (durante a aplicação). Previsão fora
     desse horário não conta;
   - chuva: `aplicacao.chuva_mm` e, se o documento fala em horas após a aplicação, o acumulado de
     `chuva_acumulada_apos_fim_mm` com o número de horas mais próximo do que o documento indica.
   Em `valor_previsto` use exatamente o número da previsão que você comparou.
3. Só marque viola=true se o trecho trouxer o limite E a previsão o ultrapassar. Copie em `trecho` o texto
   literal do documento e use `fonte_doc` e `pagina` exatamente como vieram da ferramenta. Converta unidades
   para as do sistema.
4. Se nenhum trecho trouxer o limite, ou a previsão ficar dentro dele, viola=false e explique na
   justificativa. Faça no máximo uma busca por parâmetro; não repita buscas.

Campos de cada item: os_id, viola, parametro ("chuva"|"vento"|"umidade"|"temperatura"), limite, unidade,
janela_h (horas DEPOIS do fim da aplicação em que o limite vale), valor_previsto,
trecho, fonte_doc, pagina, justificativa. Devolva um item por OS e por limite violado.
Exemplo: "chuvas dentro de quatro (4) horas após a aplicação podem afetar o desempenho" → parametro
"chuva", limite 0, unidade "mm", janela_h 4.

Ordens de serviço em risco:
{j(os_resumidas(estado))}

Gatilhos do monitor:
{j(estado["incidente"]["gatilhos"])}

Previsão no horário de cada OS (por os_id):
{j(previsao_por_os(estado))}"""


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
