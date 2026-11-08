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
   chuva numa seção diferente). Faça consultas como "chuva após a aplicação", "condições
   meteorológicas umidade relativa temperatura", "velocidade do vento", "inversão térmica".
   Olhe os gatilhos do monitor para saber o que buscar primeiro: o risco nem sempre é chuva.
2. Compare cada limite do trecho com a "Previsão no horário de cada OS" (já calculada em código):
   - vento, umidade, temperatura, rajada, Delta T e inversão: só os valores de `aplicacao` (durante a
     aplicação). Previsão fora desse horário não conta;
   - chuva: `aplicacao.chuva_mm` e, se o documento fala em horas após a aplicação, o acumulado de
     `chuva_acumulada_apos_fim_mm` com o número de horas mais próximo do que o documento indica.
   Em `valor_previsto` use exatamente o número da previsão que você comparou. Se a previsão não trouxer
   o campo (ex.: sem `delta_t_max_c`, `rajada_max_kmh` ou `inversao`), não há como comparar: viola=false.
3. Só marque viola=true se o trecho trouxer o limite E a previsão o ultrapassar. Copie em `trecho` o texto
   literal do documento e use `fonte_doc` e `pagina` exatamente como vieram da ferramenta. Converta unidades
   para as do sistema.
4. Se nenhum trecho trouxer o limite, ou a previsão ficar dentro dele, viola=false e explique na
   justificativa. Faça no máximo uma busca por parâmetro; não repita buscas.

Campos de cada item: os_id, viola, parametro ("chuva"|"vento"|"umidade"|"temperatura"|"rajada"|
"delta_t"|"inversao"), sentido ("maximo": viola acima do limite; "minimo": viola abaixo), limite, unidade,
janela_h (horas DEPOIS do fim da aplicação em que o limite vale), valor_previsto,
trecho, fonte_doc, pagina, justificativa. Devolva um item por OS e por limite violado.
Exemplos:
- "chuvas dentro de quatro (4) horas após a aplicação podem afetar o desempenho" → parametro "chuva",
  sentido "maximo", limite 0, unidade "mm", janela_h 4.
- "Velocidade do vento entre 2 e 10 km/h" com `vento_max_kmh` 14 → parametro "vento", sentido "maximo",
  limite 10, valor_previsto 14. Com `vento_min_kmh` 1 → parametro "vento", sentido "minimo", limite 2,
  valor_previsto 1.
- "Umidade relativa do ar > 60%" com `umidade_min_pct` 48 → parametro "umidade", sentido "minimo",
  limite 60, valor_previsto 48.
- "A ausência de vento pode indicar situação de inversão térmica, que deve ser evitada" com
  `inversao` true → parametro "inversao", sentido "maximo", limite 0, valor_previsto 1.
- "Delta T entre 2 e 8 °C" com `delta_t_max_c` 11 → parametro "delta_t", sentido "maximo", limite 8,
  valor_previsto 11.

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
