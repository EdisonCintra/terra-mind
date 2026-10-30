# Contratos entre as frentes

Combinados de nomes, formatos e endereços entre Gustavo (agentes), Edison (conhecimento e clima) e Felipe (fazenda, canal e infra). Mudou um contrato? Avise os outros dois antes de fazer o merge.

Status: **proposto** (aguarda o ok do dono da outra ponta) ou **fechado**.

## Decisões de arquitetura que mudam a spec (29/10, Gustavo)

| # | Decisão | Antes, na spec | Impacto |
|---|---|---|---|
| A1 | A composição usa `Workflow` (grafo do ADK 2.x). `SequentialAgent`, `ParallelAgent` e `LoopAgent` estão `@deprecated` no `google-adk` 2.13 | `SequentialAgent` + `ParallelAgent` + `LoopAgent` | Só na frente do Gustavo |
| A2 | A aprovação é um nó que emite `RequestInput(interrupt_id="aprovacao:{plano_id}")`. A retomada manda uma `FunctionResponse(name="adk_request_input", id="aprovacao:{plano_id}")` na mesma sessão | `LongRunningFunctionTool` chamado pelo Supervisor | O callback precisa só de `incidente_id` e `plano_id` (contrato 6) |
| A3 | Modelos da OpenAI, direto (`LiteLlm`, formato `openai/<modelo>`, chave `OPENAI_API_KEY`; padrão `openai/gpt-4o-mini`; sem OpenRouter), com uma variável por agente: `MODEL_AVALIADOR`, `MODEL_AGRONOMICO`, `MODEL_OPERACIONAL`, `MODEL_SUPERVISOR` | `MODEL_NAME` único, em aberto | `.env.example` (Felipe) |
| A4 | O **Avaliador de Risco é um `LlmAgent`** que decide a violação a partir dos trechos do Qdrant (`buscar_trechos`) e devolve o limite usado com a citação. Não lê `regras_aplicacao` | `BaseAgent` determinístico sobre a tabela validada | `consultar_regras` sai do caminho crítico; ver contrato 10 |
| A5 | Se o Avaliador falhar, der timeout ou não achar trecho, a OS **não** é suspensa. O gerente recebe um alerta e o incidente vai para `aguardando_decisao_manual` | A suspensão acontecia antes de qualquer LLM | Muda o argumento "falha do LLM nunca deixa a aplicação correr" no pitch |
| A6 | `janelas_validas` é uma função determinística em `agents/rules/`, dona: Gustavo | Ferramenta do MCP | Fecha o contrato 9 do Felipe |
| A7 | NÃO encerra (OS suspensa, decisão manual). Ver opções re-planeja com a próxima janela, até 2 vezes | NÃO e Ver opções re-planejavam | Texto dos botões não muda |

## Contratos

| # | Contrato | Combinado | Entre | Status |
|---|---|---|---|---|
| 1 | Nome do produto | `erp.insumos.produto` = `produto` usado nos metadados do Qdrant e em `regras_aplicacao` (ex.: `"Ureia 46%"`). `erp.insumos.tipo` ∈ {`defensivo`, `fertilizante`} | Edison, Felipe | proposto |
| 2 | Containers e portas | agents 8000 · conhecimento 8010 · monitor 8020 · erp-mcp 8030 · painel 8080 · postgres 5432 · qdrant 6333. `conhecimento` e `monitor` são containers próprios | todos | proposto |
| 3 | Abertura de incidente | O monitor faz INSERT em `agente.incidentes` com `status = 'aberto'` e chama `POST {AGENTS_URL}/incidentes/{id}/iniciar` (202, idempotente). `previsao_snapshot` traz **todas as horas** devolvidas (5 dias, `forecast_days=5`), não só a janela de risco, porque `janelas_validas` procura a próxima janela nela | Edison | proposto |
| 4 | Estados do incidente | `aberto` → `aguardando_aprovacao` → `encerrado`, com o desvio `aguardando_decisao_manual`. Só `agents` muda o status depois da criação. O monitor deduplica por `status <> 'encerrado'`, então `aguardando_decisao_manual` bloqueia incidente novo no mesmo talhão | Edison, Felipe | proposto |
| 5 | Ferramentas MCP do ERP | Lista da seção 7.3 do plano do Felipe, em `http://erp-mcp:8030/mcp` (streamable HTTP), com `mcp>=1.24,<2` dos dois lados (API `mcp.server.fastmcp`). Escritas devolvem `{"ok": true, ...}` ou `{"ok": false, "erro": "<texto>"}`. Leituras podem devolver a lista ou o dict direto. `janelas_validas` **não** entra no MCP (A6). Campos que o `agents` lê: `listar_os` → `id, talhao_id, status, tipo_operacao, insumo_id, produto, tipo_insumo` (`i.tipo AS tipo_insumo`)`, dose_por_ha, unidade, preco_unitario, inicio_previsto, duracao_horas, maquina_id, operador_id, talhao, area_ha`; `consultar_talhao` → `nome, cultura, estagio_fenologico, area_ha`; `disponibilidade_maquinas` → `id` (só máquinas `disponivel` sem OS `agendada`/`reagendada` sobreposta); `listar_operadores` → `id, telegram_chat_id`. Datas chegam em ISO 8601 com fuso | Felipe | proposto |
| 6 | Resposta do gerente | O Telegram chama `ao_responder(Resposta(incidente_id, plano_id, resposta, respondido_por))`, com `resposta` ∈ {`SIM`, `NAO`, `OPCOES`}. O painel chama `POST {AGENTS_URL}/incidentes/{id}/resposta` com `{"plano_id", "resposta", "respondido_por"}`. Os dois caminhos caem na mesma função. Resposta repetida para o mesmo plano → 409, sem efeito | Felipe | proposto |
| 7 | Onde o bot roda | `TelegramChannel` roda dentro do processo `agents`: o lifespan do FastAPI chama `channels.base.criar_canal(CHANNEL, ao_responder)` e `await canal.iniciar()`; no desligamento, chama `canal.parar()` se existir. O `agents` importa `channels` se estiver instalado na imagem e, se não estiver, usa um canal que só escreve no log. `ao_responder` recebe o `Resposta` do Felipe (`incidente_id`, `plano_id`, `resposta`, `respondido_por`) | Felipe | proposto |
| 8 | Schema `agente` | Tabelas da seção 6.3 do plano do Felipe, mais: `planos.criado_em timestamptz NOT NULL DEFAULT now()` e `planos.escalado_em timestamptz` (timeout, A2). `agents` grava `planos` (INSERT e resposta) e `incidentes.status`. O MCP só **lê** `planos` para checar aprovação | Felipe | proposto |
| 9 | Sessões do ADK | `DatabaseSessionService` no mesmo Postgres, em `postgresql+asyncpg`, com `search_path=agente`: as tabelas do ADK ficam no schema `agente` e são criadas pelo próprio ADK, não por migration | Felipe | proposto |
| 10 | Conhecimento usado pelos agentes | Caminho crítico: `buscar_trechos(consulta, produto, secao)` (Task 11 do Edison) e `GET /fatores-perda?tipo_insumo=&condicao=`. `consultar_regras` continua existindo, mas nenhum nó depende dela (A4). Recomendação ao Edison: despriorizar as Tasks 8 e 9 e priorizar chunking e metadados (`produto`, `secao`, `pagina`), que agora decidem a avaliação | Edison | proposto |
| 11 | Condição do fator de perda | `condicao` = tipo do gatilho do monitor: `chuva` · `vento` · `umidade` · `temperatura`. Chave única `(tipo_insumo, condicao)`, como na Task 2 do Edison | Edison | proposto |
| 12 | Fuso e unidades | `America/Sao_Paulo`, todo `datetime` com fuso. Chuva em mm, vento em km/h, umidade em %, temperatura em °C, tempo em horas. Dose na mesma unidade de `insumos.unidade` | todos | proposto |
| 13 | Variáveis de ambiente de `agents` | `DATABASE_URL`, `ERP_MCP_URL`, `CONHECIMENTO_URL`, `OPENAI_API_KEY`, `MODEL_AVALIADOR`, `MODEL_AGRONOMICO`, `MODEL_OPERACIONAL`, `MODEL_SUPERVISOR`, `CHANNEL`, `TELEGRAM_BOT_TOKEN`, `GERENTE_CHAT_ID`, `APPROVAL_TIMEOUT_MIN`, `PYTHONUTF8=1`. Lista completa com os padrões em `agents/README.md` | Felipe | proposto |
| 14 | Migrations | SQL numerado em `db/migrations/`: `001_erp.sql`, `002_agente.sql`, `010_conhecimento.sql`, `900_seed.sql` | Edison, Felipe | proposto |
