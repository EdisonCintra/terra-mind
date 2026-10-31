# TerraMind · Contratos e decisões do time

Fonte única dos combinados entre as frentes: nomes, formatos, endereços e decisões que mudam a spec. Com um contrato fechado, cada frente programa sozinha sem quebrar a outra.

**Como manter:**
- Quem muda um contrato atualiza esta página no mesmo PR da mudança e avisa no grupo.
- Não renumerar. Contrato novo entra no fim; contrato que deixou de valer fica riscado, com a data.
- Status: **Fechado** (combinado pelos envolvidos), **Proposto** (escrito por uma frente, aguarda ok), **Aberto** (sem proposta fechada).
- Os planos em [docs/plans/](plans/) citam os números daqui.

Última atualização: 31/10/2026 (Gustavo: `feat/agents` alinhada com D2 e D3 no merge com a `main`).

## Decisões

| ID | Decisão | Quem | Data | Status |
|---|---|---|---|---|
| A1 | Composição usa `Workflow` (grafo do ADK 2.x). `SequentialAgent`, `ParallelAgent` e `LoopAgent` estão `@deprecated` no `google-adk` 2.13 (impacto só na frente do Gustavo) | Gustavo | 29/10 | Fechado |
| A2 | Aprovação emite `RequestInput(interrupt_id="aprovacao:{plano_id}")` e retoma com `FunctionResponse(name="adk_request_input", id="aprovacao:{plano_id}")` na mesma sessão; callback precisa só de `incidente_id` e `plano_id` | Gustavo | 29/10 | Fechado |
| A3 | Modelos da OpenAI direto via `LiteLlm` (formato `openai/<modelo>`, chave `OPENAI_API_KEY`, padrão `openai/gpt-4o-mini`; sem OpenRouter), com uma variável por agente: `MODEL_AVALIADOR`, `MODEL_AGRONOMICO`, `MODEL_OPERACIONAL`, `MODEL_SUPERVISOR` | Gustavo | 29/10 | Fechado |
| A4 | O Avaliador de Risco é um LLM e decide a violação a partir dos trechos do Qdrant (`buscar_trechos`), citando fonte e página | Gustavo | 29/10 | Fechado |
| A5 | Se o Avaliador falhar ou não achar evidência, a OS **não** é suspensa: o gerente é alertado e o incidente fica em `aguardando_decisao_manual` | Gustavo | 29/10 | Fechado |
| A6 | `janelas_validas` é uma função determinística em `agents/rules/`, dona: Gustavo (= D4) | Gustavo | 29/10 | Fechado |
| A7 | `NAO` encerra (OS suspensa, decisão manual); `OPCOES` re-planeja com a próxima janela, até 2 vezes | Gustavo | 29/10 | Fechado |
| D1 | Decisão só por RAG: sem extração de dados estruturados, sem `regras_aplicacao`, `tolerancia_fenologica`, revisão manual nem `consultar_regras` | Gustavo | 29/10 | Fechado |
| D2 | **Foco só em defensivos agrícolas** (fungicida, herbicida, inseticida). Adubação sai do escopo: fertilizante não tem bula no Agrofit e a referência técnica não garante um limite de chuva citável. Cenário da demo: chuva intensa × pulverização de defensivo agendada | Edison, Gustavo, Felipe | 29/10 | Fechado |
| D3 | Monorepo com **um ambiente Python na raiz** (`pyproject.toml` + `uv.lock`); cada serviço é uma pasta com código direto, sem pasta aninhada. A pasta da base de conhecimento se chama `base_agricola/`; schema, container e variável continuam `conhecimento` (obs.: `origin/feat/agents` foi aberta antes do PR #1 e alinhará no rebase/merge) | Edison, Gustavo, Felipe | 29/10 | Fechado |
| D4 | `janelas_validas` é código determinístico em `agents/rules/` (Gustavo), não ferramenta do MCP | Gustavo | 29/10 | Fechado (spec) |
| D5 | **Bulas da demo:** só defensivos cuja bula traz o limite de chuva escrito: Abacus HC (p. 7) e Ativum (p. 6), fungicidas BASF; Brucia (p. 9) e Amplexus (p. 5), herbicidas. **Produto da OS do cenário: Abacus HC** (registrado para soja e milho) | Edison | 30/10 | Proposto: aguarda ok do Felipe |

## Contratos

Números de 1 a 12 seguem o plano do Gustavo (consolidados com `origin/feat/agents`). De 13 em diante são contratos complementares entre as frentes.

| # | Contrato | Combinado | Entre | Status |
|---|---|---|---|---|
| 1 | Nome do produto | `erp.insumos.produto` tem exatamente o mesmo texto que o `produto` do `fontes.yaml` e o `Trecho.produto` no Qdrant. `erp.insumos.bula_id` = `conhecimento.documentos.id`. O Avaliador filtra os trechos por esse nome | Felipe, Edison | Aberto: falta escolher o defensivo no Agrofit |
| 2 | Migrations | SQL numerados em `db/migrations/`, rodados em ordem alfabética pelo Postgres na criação do banco: `001_erp.sql`, `002_agente.sql` (Felipe), `010_conhecimento.sql` (Edison), `900_seed.sql` (Felipe) | Felipe, Edison, Gustavo | Fechado |
| 3 | Abertura de incidente | O Monitor faz INSERT em `agente.incidentes` com `status = 'aberto'`, `gatilho` = lista de gatilhos (`tipo`, `valor`, `limiar`, `inicio`, `fim`) e `previsao_snapshot` = as 120 h da previsão; depois chama `POST {AGENTS_URL}/incidentes/{id}/iniciar` (202, idempotente). Só abre incidente num talhão sem nenhum incidente com status diferente de `encerrado`. Depois da criação, só `agents` muda o status (`aberto` → `aguardando_aprovacao` → `encerrado`, ou `aguardando_decisao_manual`) | Edison, Gustavo, Felipe | Fechado |
| 4 | Endereço do MCP | `http://erp-mcp:8030/mcp`, transporte streamable HTTP, pacote `mcp>=1.24,<2` (`mcp.server.fastmcp`) dos dois lados | Felipe, Gustavo | Fechado |
| 5 | Ferramentas MCP | Nomes da seção 7.3 do plano do Felipe. Escritas devolvem sempre `{"ok": true, ...}` ou `{"ok": false, "erro": "<texto>"}`; leituras devolvem lista/dict direto ou `{"ok": true, ...}` (ambos aceitos por `ErpMcp`). `suspender_os` só faz `agendada → suspensa_preventiva`; as demais escritas exigem plano aprovado. Sem `janelas_validas` (A6/D4) | Felipe, Gustavo | Fechado |
| 6, 7 | Canal e resposta do gerente | `channels.base.criar_canal(nome, ao_responder)`, `Resposta(incidente_id, plano_id, resposta, respondido_por)` com `resposta` ∈ {`SIM`, `NAO`, `OPCOES`}. O lifespan do `agents` chama `await canal.iniciar()` e no shutdown `await canal.parar()` (ou desliga `canal.app`). O Telegram chama `ao_responder` dentro do processo `agents`; o painel chama `POST {AGENTS_URL}/incidentes/{id}/resposta` com `{plano_id, resposta, respondido_por}`. Resposta repetida para o mesmo plano → 409 no HTTP; no Telegram, o `ao_responder` do `agents` lança `channels.base.DecisaoJaRegistrada` e o canal responde "decisão já registrada" (proposto por Felipe em 30/10; implementado no `agents` em 31/10). **Polling:** um token do Telegram aceita um único processo em polling; não rode o `agents` com `CHANNEL=telegram` junto com o `teste_bot`, com outro `agents` ou com a VM usando o mesmo token, senão aparece `Conflict: terminated by other getUpdates request`. Use um bot por pessoa ou ambiente, ou `CHANNEL=painel` nos demais | Felipe, Gustavo | Fechado |
| 8, 9 | Tabelas do schema `agente` | `agente.planos` com `criado_em timestamptz NOT NULL DEFAULT now()` e `escalado_em timestamptz`; `agents` grava `planos` e `incidentes.status` e o MCP só lê `planos` para checar aprovação. Sessões do ADK criadas pelo próprio `DatabaseSessionService` (`postgresql+asyncpg`, `search_path=agente`) | Felipe, Gustavo | Fechado |
| 10 | `buscar_trechos` | `buscar_trechos(consulta: str, produto: str \| None = None, secao: str \| None = None) -> {"trechos": [{texto, fonte_doc, pagina, secao, produto}]}`, sobre `POST {CONHECIMENTO_URL}/trechos/busca`. Lista vazia = o documento não traz o limite. Erro de rede → `{"trechos": [], "erro": "servico_conhecimento_indisponivel"}` | Edison, Gustavo | Fechado |
| 11 | Fatores de perda | `GET {CONHECIMENTO_URL}/fatores-perda?tipo_insumo=&condicao=` → `{tipo_insumo, condicao, fator, fonte_ou_estimativa}` ou 404. `condicao` ∈ {`chuva`, `vento`, `umidade`, `temperatura`}, os mesmos valores de `ViolacaoOs.parametro`. Sem fator, a perda fica em branco | Edison, Gustavo | Fechado |
| 12 | Unidades e fuso | Vento em km/h, temperatura em °C, umidade em %, chuva em mm, tempo em horas; dose na mesma unidade de `insumos.unidade`; todo `datetime` em ISO 8601 com fuso `America/Sao_Paulo` | todos | Fechado |
| 13 | Portas | agents 8000 · conhecimento 8010 · monitor 8020 · erp-mcp 8030 · painel 8080 · postgres 5432 · qdrant 6333 (`conhecimento` e `monitor` em containers próprios) | todos | Fechado |
| 14 | Botão "Simular chuva" | O painel chama `POST {MONITOR_URL}/replay` com `{"cenario": "chuva_80mm.json"}` e recebe `{"incidentes": [...], "erros": [...]}` (404 para cenário inexistente; 422 para caminho inválido) | Felipe, Edison | Fechado |
| 15 | Tipo do insumo | `erp.insumos.tipo` ∈ {`fungicida`, `herbicida`, `inseticida`} (D2). `fatores_perda.tipo_insumo` usa exatamente esses valores, para o `calcular_impacto` buscar o fator com o tipo que vem do ERP | Felipe, Edison, Gustavo | Fechado |
| 16 | Chaves lidas pelo `agents` no MCP | Validado contra `agents/nodes/contexto.py` e `agents/clientes/erp.py`: `listar_os` → `id` (chave da OS!), `talhao_id`, `status`, `tipo_operacao`, `insumo_id`, `produto`, `tipo_insumo` (`i.tipo AS tipo_insumo`), `dose_por_ha`, `unidade`, `preco_unitario`, `inicio_previsto`, `duracao_horas`, `maquina_id`, `maquina_tipo` (`m.tipo AS maquina_tipo`, opcional), `operador_id`, `talhao` (`t.nome AS talhao`), `area_ha`; `consultar_talhao` → `id`, `nome`, `cultura`, `estagio_fenologico`, `area_ha`, `lat`, `lon`, `tipo_solo`; `disponibilidade_maquinas` → máquinas `disponivel` sem OS `agendada`/`reagendada` sobreposta (`id`, `nome`, `tipo`, `status`); `listar_operadores` → `id`, `nome`, `telegram_chat_id` | Felipe, Gustavo | Fechado |

| 16 | Variáveis dos serviços do Edison | `conhecimento`: `DATABASE_URL`, `QDRANT_URL`, `EMBEDDING_MODEL` (opcional). `monitor`: `DATABASE_URL`, `AGENTS_URL`, `MONITOR_MODE` (`replay` padrão, ou `live`), `MONITOR_INTERVAL_MIN` (30), `LIMIAR_CHUVA_MM` (20), `LIMIAR_VENTO_KMH` (15), `LIMIAR_UMIDADE_MIN_PCT` (50), `JANELA_RISCO_H` (48). `DATABASE_URL` aceita `postgresql://` ou `postgresql+psycopg://`. Ferramenta `buscar_trechos` (dentro do `agents`): `CONHECIMENTO_URL` | todos | Implementado |

## Pendências

| O quê | Com quem | Até |
|---|---|---|
| Escolher o defensivo da demo no Agrofit, com bula que traga o intervalo sem chuva em número, e fechar `produto` + `bula_id` (contrato 1) | Edison, Felipe | dia 2 |
| ~~Alinhar `origin/feat/agents` com D2 (seed demo sem ureia) e D3 (ambiente Python na raiz) no rebase/merge com a `main`~~ feito em 31/10: `agents/` sem pacote aninhado, dependências no `pyproject.toml` da raiz, cenário da demo com Abacus HC (bula p. 7) | Gustavo | dia 2 |
