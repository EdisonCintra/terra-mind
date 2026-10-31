# Arquitetura para desenvolvimento

Oct 27, 2026 · @Gustavo Barbosa · revisado em 29/10/2026 (decisões A1–A7 e contratos em `docs/contratos.md`)

## Decisões fechadas

O TerraMind será entregue como um protótipo funcional de ponta a ponta para um único cenário (chuva intensa × pulverização de defensivo agrícola agendada; adubação saiu do escopo, ver D2 em `docs/contratos.md`), construído em 7 dias por três pessoas e apresentado ao vivo com vídeo de backup.

| Tema | Decisão | Motivo |
| --- | --- | --- |
| Entregáveis | Protótipo funcional, BMC, pitch de 3 min, README com inputs, lógica e outputs | Exigência do hackathon |
| Escopo da demo | Um cenário de ponta a ponta muito bem polido; ZARC e irrigação como roadmap | 7 dias, três pessoas |
| Autonomia | Ações reversíveis são autônomas (suspender a ordem de serviço, só com violação citada pelo Avaliador); ações que comprometem recursos exigem aprovação; sem resposta, a suspensão se mantém; sem evidência, o sistema não suspende e alerta o gerente | O pior erro possível vira uma pausa desnecessária ou uma decisão humana |
| Clima | Open-Meteo real + modo replay com cenário gravado | Prova que é real e garante o evento extremo na demo |
| ERP | Mini-ERP próprio em Postgres, exposto por servidor MCP, com tela de acompanhamento | Sem ERP real disponível; MCP materializa o acesso controlado |
| IoT | Leituras simuladas de umidade do solo, secundárias | O gatilho principal é o clima |
| Base de conhecimento | O Avaliador de Risco (LLM) decide a violação a partir dos trechos do Qdrant e cita fonte e página; a tabela validada sai do caminho crítico (revisado em 29/10, ver `docs/contratos.md` A4) | Uma fonte de verdade só, sem extração offline no caminho da demo |
| Impacto financeiro | Calculado a partir de dose, área e preço do mini-ERP, com fator de perda citado ou declarado como estimativa | Responder "de onde veio esse número" |
| Orquestração | Google ADK 2.x `Workflow` (grafo): nós em código, quatro agentes LLM (Avaliador, Agronômico, Operacional, Supervisor) e Validador em código; aprovação por `RequestInput` retomada com `FunctionResponse` | `SequentialAgent`/`LoopAgent` estão descontinuados no ADK 2.13; a pausa não depende de o LLM chamar uma ferramenta |
| Sessões | `DatabaseSessionService` em Postgres | Incidente sobrevive a restart e à espera do gerente |
| Canal | Telegram com botões inline e long polling; painel web como fallback; interface `NotificationChannel` | Sem burocracia, sem URL pública, troca fácil para WhatsApp |
| Infra | `docker-compose` em VM pequena no GCP + cópia local idêntica | Monitor e polling são processos contínuos |
| Modelo | OpenAI direto via `LiteLlm` (padrão `openai/gpt-4o-mini`, o mais barato), uma variável por agente (`MODEL_AVALIADOR`, `MODEL_AGRONOMICO`, `MODEL_OPERACIONAL`, `MODEL_SUPERVISOR`) | Troca de modelo sem mudar código |
| Plano por incidente | Um incidente gera um plano com uma opção por OS suspensa: uma mensagem e um SIM para todas | Mais simples para o gerente e para a demo |
| Respostas do gerente | SIM executa; "Ver opções" re-planeja com a próxima janela (até 2 vezes); NÃO encerra com a OS suspensa e pede decisão manual | Evita laço infinito na demo |
| Timeout | Sem resposta em `APPROVAL_TIMEOUT_MIN`, o alerta é escalonado uma vez e a sessão segue pausada; um SIM tardio vale se a janela for revalidada | O gerente pode responder depois sem perder o plano |
| Janelas válidas | `janelas_validas` é código determinístico em `agents/rules/` (Gustavo), não ferramenta do MCP | Precisa de previsão e limite, que não são dados do ERP |

## Visão de componentes

&#91;embedded content: componentes do TerraMind · 9 partes\]

O Monitor abre o incidente, o Avaliador decide a violação com citação e a OS é suspensa, o Agronômico e o Operacional analisam em paralelo usando só ferramentas, o Supervisor concilia e o Validador em código confere o plano antes do pedido de aprovação no Telegram. (O diagrama embutido é anterior à revisão de 29/10 e não mostra o Avaliador como LLM.)

## Agentes e nós

São quatro agentes LLM (Avaliador, Agronômico, Operacional e Supervisor) cercados por nós em código: o Avaliador lê os trechos das bulas e decide a violação com citação, os outros analisam e negociam, todo valor em R$ vem de `calcular_impacto` em código, e o plano só sai depois de um Validador em código.

> Revisado em 29/10 (decisões A1–A7 em `docs/contratos.md`). Plano de implementação: `docs/plans/2026-10-29-agentes-gustavo.md`.

| Nó | Tipo no ADK | Usa LLM | Responsabilidade | Ferramentas |
| --- | --- | --- | --- | --- |
| Monitor | Serviço próprio (Edison), dispara o incidente | Não | Compara previsão com limiares e abre um incidente | Cliente Open-Meteo, leitor de cenário |
| Contexto | nó função | Não | Busca ordens de serviço agendadas na janela de risco | MCP do ERP (leitura) |
| Avaliador de Risco | `LlmAgent` com `output_schema` | Sim | Decide, a partir dos trechos da bula, se a previsão viola o uso de cada OS e cita fonte e página | `buscar_trechos` (Qdrant) |
| Ação Preventiva | nó função | Não | Suspende a ordem de serviço (única ação autônoma) e calcula a perda em R$ | MCP do ERP (escrita restrita a status), `calcular_impacto` |
| Agronômico | `LlmAgent`, ramo paralelo | Sim | Interpreta o risco para a cultura e cita a fonte | `buscar_trechos`, `consultar_talhao` |
| Operacional | `LlmAgent`, ramo paralelo | Sim | Propõe como reorganizar máquinas e equipes | `listar_os`, `disponibilidade_maquinas`, `listar_operadores` (MCP), `janelas_validas` |
| Supervisor | `LlmAgent` com `output_schema` | Sim | Concilia as duas análises, compara o custo das opções e redige a mensagem | `calcular_impacto`, `janelas_validas` |
| Validador | função em código | Não | Confere se a janela respeita o limite citado pelo Avaliador e se a máquina está livre; devolve o motivo ou aprova | `janelas_validas`, MCP do ERP (leitura) |
| Aprovação | nó função com `RequestInput` | Não | Pausa a sessão até o callback do gerente | Canal |
| Executor | nó função | Não | Após o SIM: revalida, reagenda, aloca máquina, reserva estoque, notifica | MCP do ERP (escrita), canal |

A composição no ADK (`google.adk.workflow.Workflow`):

```
Nós `Agent` em [colchetes]; o resto são funções. Todo roteador tem `DEFAULT_ROUTE` → decisao_manual.

START → contexto ─┬─ sem_os ───────────────────────────────────→ encerrar
                  ├─ (default: ERP fora) ──────────────────────→ decisao_manual
                  └─ com_os → [avaliador_risco] → rota_avaliacao ─┬─ sem_violacao → encerrar
                                                                  ├─ falhou ──────→ decisao_manual (OS segue agendada)
                                                                  └─ violacao → acao_preventiva
acao_preventiva ─┬─ suspensas → [agronomico]  ─┐
                 └─ suspensas → [operacional] ─┴→ analises (join) → preparar_plano
preparar_plano → [supervisor] → validador ─┬─ valido → aprovacao (pausa) → rota_resposta
                      ↑____ revisar (≤2) ___┤
                                            └─ reprovado → decisao_manual
rota_resposta ─┬─ SIM → executor ─┬─ ok → encerrar
               │                  ├─ revalidar → preparar_plano
               │                  └─ falhou → decisao_manual
               ├─ OPCOES (≤2) → preparar_plano
               └─ NAO → decisao_manual
```

Os especialistas escrevem no estado da sessão via `output_key` e o Supervisor lê os dois. A aprovação é retomada pelo callback do Telegram ou do painel na mesma sessão, com uma `FunctionResponse` de id `aprovacao:{plano_id}`.

A suspensão depende do Avaliador (LLM). Se ele falhar ou não achar evidência, a OS **não** é suspensa: o gerente recebe um alerta e o incidente fica em `aguardando_decisao_manual`. Se o Validador reprovar o plano duas vezes, o fluxo termina sem plano: a ordem de serviço segue suspensa e o gerente recebe só o alerta, pedindo decisão manual.

## Fluxo do agente

&#91;embedded content: fluxo do incidente · 6 etapas, 3 decisões\]

"Ver opções" volta ao Supervisor com a próxima janela (até 2 vezes); NÃO encerra com a OS suspensa e pede decisão manual; sem resposta, a ordem de serviço segue suspensa e o alerta é escalonado uma vez, e um SIM tardio ainda vale se a janela for revalidada. Esta seção é a base do README.

| Etapa | Input | Lógica | Output |
| --- | --- | --- | --- |
| 1 · Gatilho | Previsão horária por talhão (Open-Meteo ou replay) | Chuva acumulada, vento e umidade contra limiares | Incidente aberto |
| Contexto | Incidente; ordens de serviço, insumos e máquinas via MCP | Filtra ordens de serviço dentro da janela de risco | Ordens de serviço em risco |
| 2 · Avaliação | Ordem de serviço, trechos da bula (Qdrant), previsão | Avaliador (LLM) decide a violação com citação; fórmula de perda em código | Violações citadas e perda em R$ |
| 3 · Suspensão | Ordem de serviço com violação citada pelo Avaliador | `agendada → suspensa_preventiva` | Ordem de serviço suspensa e registro de auditoria |
| 4 · Plano | Previsão de 5 dias, limite citado pelo Avaliador, disponibilidade, trechos do Qdrant | Agronômico e Operacional analisam em paralelo; Supervisor concilia; Validador confere em código (até 2 voltas) | Um plano por incidente, uma opção por OS, com citação |
| 5 · Aprovação | Plano | `RequestInput` pausa a sessão até o callback; timeout escalona uma vez | SIM, NÃO, Ver opções ou timeout |
| 6 · Execução | SIM | Revalida a janela; reagenda, aloca máquina, reserva estoque, notifica operadores | ERP atualizado e notificações |

## Modelo de dados

Um único Postgres com três schemas: `erp` (a fazenda simulada), `conhecimento` (documentos, fatores de perda e regras extraídas, estas fora do caminho crítico) e `agente` (incidentes, planos, auditoria e sessões do ADK).

| Schema | Tabela | Campos principais |
| --- | --- | --- |
| erp | talhoes | id, nome, area\_ha, cultura, data\_plantio, estagio\_fenologico, lat, lon, tipo\_solo |
| erp | insumos | id, produto, tipo, unidade, preco\_unitario, estoque, bula\_id |
| erp | maquinas | id, nome, tipo, status |
| erp | operadores | id, nome, telegram\_chat\_id |
| erp | ordens\_servico | id, talhao\_id, tipo\_operacao, insumo\_id, dose\_por\_ha, inicio\_previsto, maquina\_id, operador\_id, status |
| erp | leituras\_solo | talhao\_id, ts, umidade\_pct |
| conhecimento | regras\_aplicacao | produto, vento\_max\_kmh, horas\_sem\_chuva\_min, temp\_min, temp\_max, umidade\_min, fonte\_doc, fonte\_pagina, validado\_por |
| conhecimento | tolerancia\_fenologica | cultura, estagio, tolerancia\_encharcamento, tolerancia\_deficit\_hidrico, fonte\_doc |
| conhecimento | fatores\_perda | tipo\_insumo, condicao, fator, fonte\_ou\_estimativa |
| agente | incidentes | id, talhao\_id, gatilho, previsao\_snapshot, status, criado\_em |
| agente | planos | id, incidente\_id, opcoes\_json, opcao\_escolhida, aprovado\_por, respondido\_em, criado\_em, escalado\_em |
| agente | auditoria | incidente\_id, ts, no, acao, antes, depois, justificativa |
| agente | sessoes ADK | geradas pelo `DatabaseSessionService` (`postgresql+asyncpg`, `search_path=agente`) |

Status do incidente: `aberto` → `aguardando_aprovacao` → `encerrado`, com o desvio `aguardando_decisao_manual` (Avaliador falhou, Validador reprovou duas vezes, NÃO ou opções esgotadas). Só `agents` muda o status depois da criação, e o Monitor só abre incidente novo num talhão sem incidente diferente de `encerrado`. `previsao_snapshot` guarda as 120 h da previsão, porque `janelas_validas` procura a próxima janela nela.

Status da ordem de serviço: `agendada`, `suspensa_preventiva`, `reagendada`, `executada`, `cancelada`. O MCP só permite ao nó de Ação Preventiva a transição `agendada → suspensa_preventiva`; as demais exigem um `plano` aprovado.

O impacto financeiro é calculado sempre em código (`calcular_impacto`) pela mesma fórmula, com `fator` buscado por `(tipo_insumo, condicao)`, em que `condicao` ∈ {`chuva`, `vento`, `umidade`, `temperatura`}. Sem fator cadastrado, a perda fica em branco, sem valor inventado. O pitch mostra cada termo vindo de uma tabela:

```latex
\text{Perda} = \text{area\_ha} \times \text{dose\_por\_ha} \times \text{preco\_unitario} \times \text{fator}
```

## Base de conhecimento

A base tem duas saídas a partir dos mesmos documentos: uma coleção no Qdrant, que o Avaliador usa para decidir e citar, e uma tabela de regras validada à mão, que desde 29/10 fica fora do caminho crítico (contrato 10). A qualidade do chunking e dos metadados passa a decidir a avaliação.

**Escopo:** 5 a 8 bulas de produtos usados no cenário (Agrofit/MAPA), manuais de fenologia de soja e milho, ZARC de um município goiano.

**Pipeline offline (`ingest/`):**

1. Extrair texto dos PDFs preservando número de página.
2. Chunking por seção da bula (condições climáticas, modo de aplicação, intervalo de segurança), com metadados `produto`, `tipo_doc`, `pagina`, `secao`.
3. Indexar no Qdrant com embeddings densos e filtro por payload em `produto` e `cultura`.
4. (Despriorizado) Extração estruturada com LLM (saída em JSON validada por schema Pydantic) para `regras_aplicacao` e `tolerancia_fenologica`.
5. (Despriorizado) Revisão humana: um integrante confere cada valor contra a página citada e preenche `validado_por`. Nenhum nó do fluxo depende dessa tabela.

**Uso em tempo de execução (revisado em 29/10):** o Avaliador de Risco consulta o Qdrant filtrando pelo produto, decide a violação e devolve o limite usado com `fonte_doc` e `pagina`; a tabela validada não está no caminho crítico. O Agente Agronômico consulta o Qdrant filtrando pelo produto e pela regra violada, e recebe o trecho com `fonte_doc` e `pagina` para citar na mensagem e no relatório de auditoria.

Toda violação do Avaliador exige trecho, `fonte_doc` e `pagina` (schema Pydantic). Se a bula não trouxer o parâmetro, não há violação citável: a OS não é suspensa e o gerente é alertado. Fatores de perda continuam com fonte citada ou declarados como estimativa.

## Repositório e containers

Um monorepo com um serviço por pasta, todos subindo com `docker compose up` a partir de um `.env` único.

```
terramind/
├── agents/              # ADK: Workflow, nós, agentes LLM, tools (ver plano do Gustavo)
│   ├── agentes/         # uma pasta por agente (avaliador_risco, agronomico, operacional, supervisor, terramind)
│   ├── nodes/           # nós em código: contexto, rota_avaliacao, acao_preventiva, preparar_plano, validador, aprovacao, executor
│   ├── rules/           # janelas_validas e calculadora de impacto
│   ├── workflow.py      # grafo do incidente
│   └── runner.py        # abre sessão por incidente e retoma após aprovação
├── conhecimento/        # API de trechos, regras e fatores de perda (Edison)
├── monitor/             # loop Open-Meteo + modo replay
│   └── scenarios/       # previsões gravadas (chuva_80mm.json)
├── channels/            # NotificationChannel: telegram.py, painel.py
├── erp_mcp/             # servidor MCP do mini-ERP (leitura + escrita restrita)
├── painel/              # tela do ERP + aprovação fallback + botão simular
├── ingest/              # PDFs → chunks → Qdrant; extração de regras → Postgres
├── db/                  # migrations e seed da fazenda simulada
├── docs/                # diagramas Mermaid do README
├── docker-compose.yml
└── README.md
```

| Container | Porta | Imagem | Função |
| --- | --- | --- | --- |
| postgres | 5432 | postgres:16 | ERP, conhecimento, incidentes, planos, sessões ADK |
| qdrant | 6333 | qdrant/qdrant | Trechos das bulas e manuais |
| agents | 8000 | build local | FastAPI com runner ADK, bot do Telegram em long polling e tarefa de timeout |
| conhecimento | 8010 | build local | API de trechos, regras e fatores de perda |
| monitor | 8020 | build local | Loop Open-Meteo, replay e abertura de incidentes |
| erp-mcp | 8030 | build local | Servidor MCP exposto ao ADK (`/mcp`, streamable HTTP) |
| painel | 8080 | build local | Tela do ERP, aprovação web e gatilho do replay |

Integrações entre containers: o Monitor faz INSERT em `agente.incidentes` e chama `POST {AGENTS_URL}/incidentes/{id}/iniciar`; o painel chama `POST {AGENTS_URL}/incidentes/{id}/resposta` com `{plano_id, resposta, respondido_por}`; o Telegram chama `ao_responder` dentro do processo `agents`. Resposta repetida para o mesmo plano não tem efeito (409).

Variáveis principais: `DATABASE_URL`, `ERP_MCP_URL`, `CONHECIMENTO_URL`, `AGENTS_URL`, `MONITOR_URL`, `OPENAI_API_KEY`, `MODEL_AVALIADOR`, `MODEL_AGRONOMICO`, `MODEL_OPERACIONAL`, `MODEL_SUPERVISOR`, `TELEGRAM_BOT_TOKEN`, `GERENTE_CHAT_ID`, `CHANNEL` (`telegram` ou `painel`), `MONITOR_MODE` (`live` ou `replay`), `APPROVAL_TIMEOUT_MIN`, `PYTHONUTF8=1`.

## Plano de 7 dias

O fluxo de ponta a ponta precisa rodar com dados fake no fim do dia 3; os dias 6 e 7 são só polimento, ensaio e gravação.

| Dia | Frente A: agentes | Frente B: dados e conhecimento | Frente C: canal, painel e negócio |
| --- | --- | --- | --- |
| 1 | Contratos; esqueleto ADK com nós vazios no `Workflow` até a pausa | Schema, seed da fazenda, servidor MCP de leitura | Bot no BotFather, `NotificationChannel`, envio com botões inline |
| 2 | Retomada da sessão após restart (teste com Postgres); API `/iniciar` e `/resposta`; Contexto, Avaliador e Ação Preventiva | Coleta das bulas e chunking no Qdrant | Long polling, callback → retomada de sessão |
| 3 | `calcular_impacto`, `janelas_validas`, Agronômico e Operacional em paralelo, Supervisor e aprovação, com fakes; validar o `gpt-4o-mini` com tools + `output_schema` | Metadados e qualidade dos trechos por produto e seção (extração de regras despriorizada) | Painel: lista de ordens de serviço com status ao vivo |
| 4 | Supervisor ↔ Validador; quatro LLMs de ponta a ponta com o replay | Fatores de perda com fonte | Monitor com Open-Meteo e modo replay |
| 5 | Estabilizar prompts; Executor, timeout e escalonamento | `buscar_trechos` ajustado às consultas do Avaliador e do Agronômico | BMC e roteiro do pitch |
| 6 | Testes do cenário e casos de borda (NAO, sem resposta) | Deploy na VM e cópia local | README com diagramas; ensaio do pitch |
| 7 | Congelamento de código | Gravação do vídeo de backup | Ensaio final cronometrado em 3 min |

- [ ] Dia 2: um SIM no Telegram retoma a sessão num processo reiniciado
- [ ] Dia 3: fluxo de ponta a ponta roda com fakes
- [ ] Dia 4: quatro agentes LLM e Validador rodando de ponta a ponta
- [ ] Dia 6: demo roda na VM e localmente
- [ ] Dia 7: vídeo de backup gravado

## Riscos e perguntas da banca

Os maiores riscos são de demo, não de código: rede do evento e um número sem fonte.

| Risco | Mitigação |
| --- | --- |
| Rede do evento cai | Cópia local idêntica; vídeo de backup |
| Telegram falha no palco | `CHANNEL=painel` troca o canal sem mexer no fluxo |
| Retomada da sessão ADK após horas não funciona como esperado | Testar no dia 2, não no dia 6; se falhar, persistir o plano em `agente.planos` e abrir sessão nova no callback |
| Bula sem parâmetro explícito | O Avaliador devolve "sem violação" sem citação válida → a OS não é suspensa e o gerente é alertado |
| Avaliador (LLM) erra o limite | Citação obrigatória (trecho, fonte, página) na auditoria e no painel; cenário da demo revisado no ensaio |
| Avaliador (LLM) falha e a OS não é suspensa | Alerta imediato ao gerente; incidente em `aguardando_decisao_manual` bloqueia duplicatas |
| Modelo da OpenAI sem suporte a tools + `output_schema` | Validar no dia 3; trocar só a variável de ambiente do agente |
| API do ADK 2.x muda durante a semana | Versão fixada no `uv.lock` no dia 1 |
| Fator de perda sem fonte | Declarar como estimativa parametrizada no pitch |

| Pergunta provável | Resposta preparada |
| --- | --- |
| Por que Telegram e não WhatsApp? | Produção é WhatsApp Business; o canal é uma interface isolada |
| E se o agente errar? | O único ato autônomo é suspender, que é reversível e exige violação citada; o resto exige aprovação, e o banco recusa escrita sem plano aprovado |
| De onde veio o valor da perda? | Fórmula com área, dose, preço e fator citado |
| Onde o LLM pode alucinar? | Na leitura da bula pelo Avaliador: por isso toda violação exige trecho, fonte e página, gravados na auditoria e visíveis no painel. Valores em R$ vêm só de código, e o Validador em código barra janela que viole o limite citado ou máquina ocupada |
| E se o Avaliador falhar? | Sem evidência, o sistema não age sozinho: a OS segue agendada e o gerente é alertado para decidir |
| Isso escala para outras fazendas? | ERP real entra como outro servidor MCP; produção em Cloud Run ou Agent Engine |

**Em aberto:** se o `gpt-4o-mini` for fraco em algum agente (o Avaliador é o mais crítico), trocar só a variável `MODEL_*` dele.

## Divisão do time

Cada pessoa é dona de uma frente, e Gustavo define os contratos entre as frentes no dia 1 para que os três desenvolvam com IA em paralelo sem travar uns aos outros.

| Pessoa | Frente | Entregas |
| --- | --- | --- |
| Gustavo | Agentes e integração | Contratos do dia 1; `Workflow` no ADK; Avaliador, Agronômico, Operacional e Supervisor; Validador, `janelas_validas` e `calcular_impacto`; aprovação com retomada de sessão; Executor e timeout; API do `agents`; revisão de código; pitch |
| Edison | Conhecimento e clima | Coleta das bulas; pipeline `ingest/`; Qdrant com chunking e metadados por produto e seção; ferramenta `buscar_trechos` e fatores de perda; `consultar_regras`, extração e validação manual despriorizadas; depois, Monitor com Open-Meteo e modo replay (snapshot de 120 h) |
| Felipe | Fazenda simulada, canal e infra | Schema e seed do mini-ERP (inclui `agente.planos` com `criado_em` e `escalado_em`); servidor MCP sem `janelas_validas`; `NotificationChannel` com Telegram rodando dentro do `agents`; painel web; `docker-compose` |
