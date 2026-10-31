# TerraMind · Tarefas do Felipe (por tópico do plano)

Base: `2026-10-29-fazenda-canal-infra-felipe.md`, "Arquitetura para desenvolvimento" (revisada em 29/10) e `docs/contratos.md` (atualizado em 29/10 pelo Edison). Estimativas: P ≈ 30 min, M ≈ 1–2 h, G ≈ 3–4 h.

## Mudanças recentes que alteram o plano

Itens de `contratos.md` que o plano do Felipe ainda não reflete. Cada um virou tarefa abaixo.

| # | Mudança | Efeito na sua frente | Tarefa |
|---|---|---|---|
| D2 | Só defensivos; adubação fora do escopo (**Fechado**) | Sem ureia, sem distribuidor, sem OS de adubação. `insumos.tipo` ∈ {`fungicida`,`herbicida`,`inseticida`} (contrato 15) | T4.0, T4.2, T6.1, T6.3 |
| D3 | Um ambiente Python na raiz (`pyproject.toml` + `uv.lock`); código direto na pasta do serviço (**Fechado**) | Some o `pyproject.toml` por serviço e o `erp_mcp/erp_mcp/`; pasta do Edison é `base_agricola/` | T4.0, T3.2, T5.1, T5.3, T7.9, T9.6 |
| A3 | Modelos da OpenAI direto (`OPENAI_API_KEY`, padrão `openai/gpt-4o-mini` nas 4 `MODEL_*`, sem OpenRouter) | Muda `.env.example` | T5.2, T10.4 |
| D1, A4, A5 | Decisão só por RAG; Avaliador é LLM; sem evidência, a OS não é suspensa | O painel precisa mostrar `aguardando_decisao_manual` e a citação (fonte e página) | T9.3, T9.4 |
| D4 / A6 | `janelas_validas` fica em `agents/rules/` (Gustavo) | MCP **sem** essa ferramenta | T7.3 |
| C3 | Monitor faz INSERT em `agente.incidentes`; `gatilho` é lista (`tipo`,`valor`,`limiar`,`inicio`,`fim`) | `002_agente.sql` precisa aceitar isso; só `agents` muda o status depois | T6.2 |
| C5, C16 | Escritas do MCP devolvem `{"ok": bool, ...}`; leituras aceitam lista/dict direto ou `{"ok": true, ...}`; `listar_os` usa `id` (não `os_id`) | Chaves validadas contra `origin/feat/agents` | T7.2, T7.5 |
| C12 | Vento km/h, °C, %, mm, horas; `datetime` em `America/Sao_Paulo` | Seed e `listar_os` | T6.3, T7.2 |
| C14 | `POST {MONITOR_URL}/replay` com `{"cenario": "chuva_80mm.json"}` → `{"incidentes": [...], "erros": [...]}` | Botão "Simular chuva" | T9.5 |
| C15 | Valores de `tipo` (**Fechado** com D2) | `fungicida`, `herbicida`, `inseticida` | T4.2, T6.1 |

> Numeração: o plano do Felipe (seção 4, itens 1–9) usa números antigos. Vale sempre o de `docs/contratos.md`. As referências a "contrato 12 (aliases do `listar_os`)" e "contrato 11 (máquinas por tipo)" do arquivo anterior **não existem mais**; o formato das chaves do `listar_os` precisa ser combinado com o Gustavo (T4.5).

---

## 1. O TerraMind em um parágrafo / 2. O seu pedaço
- [x] T1.1 (P) Mapear onde seu código é chamado: `contexto`, `acao_preventiva`, `validador`, `aprovacao`, `executor`. Resultado: [fluxo-pontos-de-chamada-felipe.md](../fluxo-pontos-de-chamada-felipe.md).
- [x] T1.2 (P) Quatro peças suas: mini-ERP, `erp_mcp`, `channels`, `painel` + `docker-compose` (confirmação verbal na T4.1).

## 3. Ferramentas
- [x] T3.1 (P) Instalar Docker Desktop, Python 3.12, `uv`, DBeaver (opcional) e Node (MCP Inspector).
- [x] T3.2 (P) D3 fechado: `uv sync` na raiz e `uv.lock` versionado (risco "ADK muda na semana"). Dependências (`psycopg[binary]`, `mcp[cli]<2`, `python-telegram-bot`, `fastapi`, `uvicorn`, `jinja2`, `httpx`), `[tool.uv] package = false` e `pythonpath = ["."]` adicionados no `pyproject.toml` único (alinhado com `origin/feat/conhecimento-base`). `mcp` fixado em 1.x (`FastMCP`).
- [x] T3.3 (P) `PYTHONUTF8=1` no ambiente (Windows). Feito em 29/10: gravada como variável de usuário; validada com `sys.flags.utf8_mode = 1`. No Docker entra pelo `.env.example` (T10.4).

## 4. Contratos (dia 1) → `docs/contratos.md`
- [x] T4.0 (P) **D2 (só defensivos) e D3 (ambiente na raiz) fechados.** Validado em 30/10: merge do PR #1 pelo Gustavo (`cc4e397`) + ok do Gustavo e do Felipe registrados em `docs/contratos.md` como **Fechado** (obs.: `origin/feat/agents` foi criada antes do PR #1 e alinhará no rebase).
- [x] T4.3 (P) C2: numeração `001_erp.sql`, `002_agente.sql` (Felipe), `010_conhecimento.sql` (Edison), `900_seed.sql` (Felipe) confirmada em `origin/feat/agents` (`conftest.py` + contrato 14) e no plano do Edison (Task 2). Blocos do compose mapeados (`agents/README.md` e Task 17 do Edison).
- [x] T4.4 (P) C3 e C8/C9: `002_agente.sql` validado contra `agents/tests/sql/contrato_minimo_agente.sql`, `repositorio.py` e `test_postgres.py` do Gustavo (`incidentes`, `planos` com `criado_em`/`escalado_em`, `auditoria`; `CHECK` de status e índice único parcial por talhão compatíveis com todos os testes; sessões do ADK em `search_path=agente`).
- [x] T4.5 (P) C5 e C16: validado contra `agents/clientes/erp.py`, `agents/nodes/contexto.py` e `test_erp_mcp.py`. **Correção crítica aplicada no contrato 16:** `contexto.py` lê `linha["id"]` (e **não** `os_id`!). Leituras aceitam lista/dict direto ou `{"ok": true, ...}`; escritas devolvem sempre `{"ok": bool, ...}`.
- [x] T4.6 (P) C6/C7: validado contra `agents/canal.py` e `agents/api.py`. `agents` importa `channels.base.criar_canal(nome, ao_responder)`, chama `await canal.iniciar()` no `lifespan` e `await canal.parar()` no shutdown; `POST /incidentes/{id}/resposta` devolve 409 em resposta repetida.
- [x] T4.7 (P) C14: validado na Task 16 do Edison (`POST {MONITOR_URL}/replay` com `{"cenario": "chuva_80mm.json"}` → `200 {"incidentes": [...], "erros": [...]}`, `404` inexistente, `422` caminho inválido).
- [x] T4.9 (P) Decisões A1–A3, A6 e A7 resgatadas de `origin/feat/agents:docs/contratos.md` e consolidadas em `docs/contratos.md`. **Impacto no seu lado (A3):** `agents` usa `OPENAI_API_KEY` e padrão `openai/gpt-4o-mini` nas 4 variáveis `MODEL_*` (sem OpenRouter).

**Pendentes que ainda dependem de insumo externo do Edison (não travam banco nem MCP de código):**

- [x] T4.1 (M) Contratos 2–17, A1–A7 e D1–D5 consolidados em `docs/contratos.md`; contrato 1 **Fechado** pelo Edison (`Abacus HC`, `bula-abacus-hc`). Falta só avisar o grupo no PR.
  1. [x] T4.2 Contrato 1 fechado: `erp.insumos.produto` = `Abacus HC` e `bula_id` = `bula-abacus-hc`, iguais ao `fontes.yaml` do Edison (conferido em 31/10).
  2. [x] T4.8 `monitor/scenarios/chuva_80mm.json` conferido em 31/10: 80,0 mm entre a 14ª e a 33ª hora, seco depois; a OS do seed (+20 h, 4 mm naquela hora) cai dentro da chuva; `ReplayFonte` rebaseia o cenário para a hora atual.

## 5. Estrutura de pastas
- [x] T5.1 (P) Esqueleto do monorepo conforme D3 fechado: `db/migrations`, `erp_mcp/`, `channels/`, `painel/{templates,static}`, `docs` (sem pasta aninhada). Validado em 30/10 com `pythonpath = ["."]` e `uv sync`: pacotes `erp_mcp`, `channels` e `painel` importam direto da raiz (compatível com `from channels.base import criar_canal` do `agents`).
- [x] T5.2 (P) `.gitignore` (ignora `.env` e `.env.*`, libera `!.env.example`) e `.env.example` completo com todas as variáveis das 3 frentes (incluindo `OPENAI_API_KEY` + quatro `MODEL_*` de A3, `QDRANT_URL`, `EMBEDDING_MODEL`, `MONITOR_INTERVAL_MIN` e `PYTHONUTF8=1`). Validado com `git check-ignore`.
- [x] T5.3 (P) Dependências centralizadas no `pyproject.toml` único da raiz + `uv.lock` conforme D3 (`uvicorn[standard]`, `mcp[cli]>=1.24,<2`, `psycopg[binary]`, `python-telegram-bot`, `fastapi`, `jinja2`, `httpx` + grupo `dev` com `pytest`, `pytest-asyncio`, `ruff`). Validado com `uv sync` e `uv lock --check`.

## 6. Banco de dados
- [x] T6.1 (M) `001_erp.sql`: tabelas do ERP; `insumos.tipo` com `CHECK` (`fungicida`/`herbicida`/`inseticida`); `ordens_servico.duracao_horas`; `tipo_operacao` bloqueando `'adubacao'`; `status` com `CHECK`.
- [x] T6.2 (M) `002_agente.sql`: `incidentes` (`gatilho` e `previsao_snapshot` `jsonb` validados como array, `CHECK` de status com 4 valores, índice único parcial `WHERE status <> 'encerrado'` por talhão), `planos` (`criado_em`, `escalado_em`), `auditoria`.
- [x] T6.3 (G) `900_seed.sql`: datas relativas a `now()` e fuso `America/Sao_Paulo`; só defensivos (`Abacus HC` e `Engeo Pleno S`); talhões com coordenadas de Rio Verde/GO; 2 pulverizadores (A e B); OS 1 do cenário na janela de 24 h (`Abacus HC`, `0,3 L/ha`, Pulverizador A, João) + OS 2 e OS 3 fora da janela (`+6 days` e `+8 days`); 96 leituras de solo. Sem ureia nem distribuidor.
- [x] T6.4 (P) Borda do seed conferida (e coberta em `tests/test_db_migrations.py`): Pulverizador B 100% livre em toda a janela pós-chuva (`+36 h` a `+120 h`); estoque suficiente (`500 L >= 36 L`); dose e preço em `L`; `Abacus HC` alinhado ao cenário (confirmar `bula_id` final com Edison no dia 2).
- [x] T6.5 (P) Validar: `docker compose down -v && docker compose up -d postgres` e rodar `uv run pytest tests/test_db_migrations.py` / `SELECT` no DBeaver (aguarda ligar o engine do Docker Desktop e liberar a porta `5432`, ocupada por `postgresql-x64-18` no Windows). Validado em 30/10 no container (porta 5433 no host, porque o Postgres do Windows ocupa a 5432; use `POSTGRES_PORT=5433`): 3 OS `agendada`, 1 na janela de 24 h, zero sobreposição.
- [x] T6.6 (P) Testar o índice único: teste automatizado pronto em `tests/test_db_migrations.py::test_indice_unico_parcial_incidente_por_talhao` (aguarda o container `postgres` subir). Validado em 30/10 (`test_indice_unico_parcial_incidente_por_talhao`).

## 7. Servidor MCP do ERP
- [x] T7.1 (P) `db.py` e `registrar_auditoria` (`Jsonb`, conversão de `datetime`/`Decimal`). `erp_mcp/db.py`.
- [x] T7.2 (M) `leitura.py`: `listar_os`, `detalhar_os`, `disponibilidade_maquinas` (sobreposição com `duracao_horas`), `listar_operadores`, `consultar_talhao`, `leituras_solo`. Chaves e retorno `{"ok": ...}` conforme T4.5. Chaves do contrato 16; leituras devolvem lista/dict direto, erros `{"ok": false}`.
- [x] T7.3 (P) `server.py` com FastMCP, `streamable-http`, porta 8030, docstrings em português. **Sem** `janelas_validas` (D4). 10 ferramentas, sem `janelas_validas`.
- [x] T7.4 (P) Testado em 31/10 contra o container do `erp-mcp` (porta 8030): (1) com o `ErpMcp` do Gustavo (`listar_os` com todas as chaves do contrato 16, `consultar_talhao`, `disponibilidade_maquinas`, `listar_operadores`, `suspender_os`); (2) com o MCP Inspector em modo CLI (`tools/list` = 10 ferramentas; `tools/call listar_operadores`).
- [x] T7.5 (M) `escrita.py`: `suspender_os` com a transição no próprio `UPDATE` e auditoria na mesma transação. `erp_mcp/escrita.py`; recusa também incidente inexistente.
- [x] T7.6 (M) `reagendar_os`, `alocar_maquina`, `reservar_estoque` com `_plano_aprovado`; máquina livre; estoque suficiente; segunda chamada com o mesmo plano falha. Trava por `pg_advisory_xact_lock(plano_id)`; repetição de `reservar_estoque` detectada pela auditoria.
- [x] T7.7 (P) Garantir que sem plano aprovado só passa `agendada → suspensa_preventiva`. Coberto por `test_sem_plano_aprovado_so_passa_a_suspensao`.
- [x] T7.8 (M) `pytest`: suspender OK; suspender de novo falha; reagendar sem plano falha; reservar acima do estoque falha; `alocar_maquina` ocupada falha; `reagendar_os` repetido falha; `listar_os` devolve as chaves do contrato. 20 testes passando (`tests/test_erp_mcp.py`, `tests/test_db_migrations.py`, `tests/conftest.py` reseta o banco a cada teste).
- [x] T7.9 (P) Dockerfile do `erp_mcp` e bloco `erp-mcp` no compose (build a partir da raiz, se D3). `erp_mcp/Dockerfile`, `.dockerignore` e bloco `erp-mcp` no compose; container validado.

## 8. Canal de notificação
- [x] T8.1 (P) BotFather: `TELEGRAM_BOT_TOKEN`, `GERENTE_CHAT_ID` preenchidos no `.env` (30/10). Token só no `.env`; o `chat_id` do operador não vai no seed versionado, só por `UPDATE` local.
- [x] T8.2 (M) `channels/base.py`: `Resposta` (valida SIM/NAO/OPCOES), `AoResponder`, `NotificationChannel` (com `iniciar()` e `parar()` padrão, que o `agents` chama) e `criar_canal` (import tardio de `channels.telegram` e `channels.painel`). 6 testes em `tests/test_channels_base.py`. `criar_canal("painel")` só funciona depois da T8.8 e o `"telegram"` depois da T8.3.
- [x] T8.3 (G) `telegram.py`: botões, `callback_data` curto, `query.answer()`, remoção dos botões, `iniciar()`/`parar()`, `enviar_alerta`, `notificar_operador`. 12 testes sem rede em `tests/test_channels_telegram.py`.
- [x] T8.4 (P) `channels/teste_bot.py` com `ao_responder` falso (`uv run python -m channels.teste_bot`). Validado no Telegram real em 30/10: clique em SIM chegou ao `ao_responder`.
- [ ] T8.5 (G) **Dia 2, com o Gustavo:** SIM no Telegram retoma a sessão num processo reiniciado (maior risco técnico).
- [x] T8.6 (P) Resposta repetida (409): `DecisaoJaRegistrada` em `channels/base.py`; o Telegram mostra "decisão já registrada", sem erro. **Falta o ok do Gustavo**: o `ao_responder` dele precisa lançar essa exceção (registrado no contrato 6/7).
- [x] T8.7 (P) Um único polling por token: `TelegramChannel` loga uma mensagem clara no `Conflict`, aviso no `.env.example`. Falta só enviar o aviso ao grupo (texto abaixo).
  > Aviso ao time: cada token só aceita UM processo em polling. Não rode o `agents` com `CHANNEL=telegram` e o `teste_bot` (ou outro `agents`, ou a VM) ao mesmo tempo com o mesmo token; senão aparece `Conflict: terminated by other getUpdates request`. Um bot por pessoa/ambiente, ou `CHANNEL=painel` nos demais.
- [x] T8.8 (M) `channels/painel.py` (`PainelChannel`): o alerta vira linha em `agente.auditoria` (`no='painel'`, `acao='enviar_alerta'`); `notificar_operador` também é registrado; `pedir_aprovacao` não cria nem altera planos. `enviar_alerta` ganhou `incidente_id` opcional para ligar o alerta ao incidente (compatível com a chamada antiga). 4 testes em `tests/test_channels_painel.py`.
- [x] T8.9 (P) Escalonamento e decisão manual: os textos ficaram com o Gustavo (`agents/nodes/desfechos.py`, `agents/timeout.py`), mais completos que os meus; removi `channels/mensagens.py`. Como o `agents` não passa `incidente_id` em `enviar_alerta`, o `PainelChannel` o extrai do início do texto ("Incidente N:") para ligar o alerta ao incidente na linha do tempo e na faixa vermelha. `escalado_em` é gravado pelo `agents`.

## 9. Painel web
- [x] T9.1 (M) `painel/main.py` (FastAPI + Jinja2) com `htmx.min.js` 2.0.4 em `painel/static/`, sem CDN nem login. Rotas `/`, `/os`, `/parcial/os`, `/health`. Rodar local: `uv run uvicorn painel.main:app --port 8080`.
- [x] T9.2 (M) Tela 1, OS: `hx-trigger="every 2s"`, status colorido (verde agendada, amarelo suspensa, azul reagendada), fundo escuro e fonte grande. 5 testes em `tests/test_painel.py`.
- [x] T9.3 (M) Tela 2, `/timeline`: auditoria do incidente (o mais recente por padrão, `?incidente=` troca), atualizada a cada 2 s; mostra `campo: antes → depois` e destaca a citação `(fonte: X, p. N)` do motivo como selo. O formato vem de `acao_preventiva.py` do Gustavo (`origin/feat/agents`).
- [x] T9.4 (M) Tela 3, `/aprovacao`: planos com `respondido_em IS NULL` (mensagem de `opcoes_json->>'mensagem'`), botões SIM/NÃO/Ver opções que chamam `POST {AGENTS_URL}/incidentes/{id}/resposta`; 409 vira aviso "decisão já registrada"; selo "escalado"; faixa vermelha (`/parcial/alertas`, em todas as telas) para `aguardando_decisao_manual`.
- [x] T9.5 (P) Tela 4, `/simular`: `POST {MONITOR_URL}/replay` com `chuva_80mm.json`; mostra `incidentes` e `erros`; 404, 422 e Monitor fora do ar viram mensagem. **Falta ensaiar contra o Monitor real do Edison.**
- [x] T9.6 (P) `painel/Dockerfile` (contexto na raiz) e bloco `painel` no compose; validado em container na porta 8080.

## 10. docker-compose
- [x] T10.1 (P) Dia 1: compose só com `postgres` (healthcheck, volume, migrations e fuso `America/Sao_Paulo` em `docker-compose.yml`).
- [x] T10.2 (P) Dia 2: `erp-mcp` e `qdrant`. Dia 3: `painel`. Os três no compose; `qdrant` com volume `qdrantdata` (porta 6333).
- [x] T10.3 (P) Deixar comentários para `agents`, `conhecimento` e `monitor`; cada dono acrescenta o seu bloco.
- [x] T10.4 (P) `.env.example` com `DATABASE_URL`, `ERP_MCP_URL`, `CONHECIMENTO_URL`, `AGENTS_URL`, `MONITOR_URL`, `QDRANT_URL`, `OPENAI_API_KEY` (A3 do Gustavo, padrão `openai/gpt-4o-mini` nas quatro `MODEL_*`), `TELEGRAM_BOT_TOKEN`, `GERENTE_CHAT_ID`, `CHANNEL`, `MONITOR_MODE`, `APPROVAL_TIMEOUT_MIN`, `PYTHONUTF8=1` (entregue junto com a T5.2).
- [-] ~~T10.5 (P) Dockerfile padrão com `uv sync --frozen` e o `CMD` de cada serviço.~~ **Cancelada**: o Edison resolve sozinho (Dockerfiles dele).
- [-] ~~T10.6 (M) Dia 6: deploy na VM do GCP com o Edison; cópia local idêntica; `docker compose up` do zero nas duas.~~ **Cancelada**: o Edison resolve sozinho (deploy na VM).

## 11. Plano dia a dia
| Dia | Tarefas |
|---|---|
| 1 | T3.*, T4.0–T4.8, T5.*, T6.1–T6.6, T7.1–T7.4, T8.1, T8.4, T10.1 |
| 2 | T4.9; T7.5–T7.9, T8.2, T8.3, T8.5–T8.7, T10.2 (`erp-mcp`, `qdrant`); revisar o contrato mínimo do Gustavo contra suas migrations |
| 3 | T9.1, T9.2, T9.6, T10.2 (`painel`); ensaio ponta a ponta com fakes |
| 4 | T9.3, T9.4, T9.5, T8.8, T8.9; testar `CHANNEL=painel`; ensaiar o replay real (OS amarelas → plano → SIM → OS azuis) |
| 5 | T11.1, T11.3 |
| 6 | T10.6, T11.4, T11.5 |
| 7 | T11.6 |

- [-] ~~T11.1 (G) BMC com o time.~~ **Cancelada** a pedido do Felipe (31/10).
- [-] ~~T11.3 (M) Ajustes do painel pedidos pelo time; a OS fica amarela segundos depois de "Simular chuva".~~ **Cancelada** a pedido do Felipe (31/10).
- [x] T11.4 (M) Seção do README: mini-ERP, MCP, canal, painel e como subir (`README.md`, seção "frente do Felipe").
- [-] ~~T11.5 (M) Casos de borda: NÃO, sem resposta (escalonamento uma vez), SIM tardio, Ver opções 2×, clique duplo (409), Telegram fora, `aguardando_decisao_manual`.~~ **Cancelada** a pedido do Felipe (31/10).
- [-] ~~T11.6 (M) Dia 7: sem código novo; ensaio cronometrado de 3 min e apoio ao vídeo de backup.~~ **Cancelada** a pedido do Felipe (31/10).

**Marcos:** dia 2, SIM no Telegram retoma a sessão num processo reiniciado; dia 3, fluxo com fakes; dia 6, demo na VM e local.

## 12. Uso de IA
- [ ] T12.1 Passar a seção do plano e pedir o teste junto; conferir nomes de status e ferramentas contra `contratos.md`; conferir no banco depois de toda escrita.

## 13. Pedir ajuda
- Travado mais de 1 h; mudança de contrato; qualquer alteração em `agente.*` além do plano.

## Dependências

| Preciso de | Quem | Quando |
|---|---|---|
| Defensivo da demo no Agrofit: texto exato de `produto` e `bula_id` (C1) | Edison | Dia 2 |
| Rebase de `origin/feat/agents` com `main` (D2/D3) e teste de retomada de sessão | Gustavo | Dia 2 |
| `POST /replay` do Monitor rodando + `chuva_80mm.json` | Edison | Dia 4 |

| Entrego | Para | Quando |
|---|---|---|
| Migrations `001`/`002`/`900` | Gustavo, Edison | Dia 1–2 |
| MCP de leitura | Gustavo | Dia 1 |
| Pacote `channels` | Gustavo | Dia 2 |
| Base do compose e `.env.example` | Edison, Gustavo | Dia 1 |

## Resumo do que falta (31/10)

Concluídas em 31/10: T4.1, T4.2, T4.8 e T7.4.

Legenda: `[ ]` não feita · `[~]` meia feita · `[-]` cancelada. Tudo o que não aparece aqui está `[x]`.

### Meia feitas `[~]`

Nenhuma.

### Canceladas `[-]` (31/10)

- **T10.5 e T10.6** (Dockerfiles do Edison e deploy na VM): o Edison resolve sozinho.
- **T11.1, T11.3, T11.5 e T11.6** (BMC, ajustes do painel, casos de borda de ponta a ponta e ensaio cronometrado): canceladas a pedido do Felipe. A T11.2 (pitch) já havia saído da lista.

### Não feitas `[ ]`

A T11.2 (roteiro do pitch) saiu da lista do Felipe: o pitch não é com ele.


| Tarefa | Resumo | Quando | Depende de |
|---|---|---|---|
| T8.5 (G) | SIM no Telegram retoma a sessão num processo reiniciado (**maior risco técnico**) | Atrasada (dia 2) | Gustavo; `OPENAI_API_KEY` (ou `DEMO_LLM=fake`) |
| T12.1 | Prática contínua (testes junto, nomes conferidos contra `contratos.md`, conferir no banco); marcar ao fechar o projeto | Contínua | — |

### Resolvido em 31/10 (pendências deixadas pelo Edison no contrato)

- `talhao_id` no replay: o painel manda `talhao_id: 1` (`TALHAO_DEMO` muda), então o Talhão 2 não abre incidente vazio.
- `AGENTS_URL` e `MONITOR_URL` no `environment` do `painel` no compose.
- `.env.example` com `CHANNEL=painel`; o `TelegramChannel` sem token ou `chat_id` falha com mensagem clara.

### Bloqueio atual (Gustavo)

- Em 31/10, o Avaliador entrou em laço de `buscar_trechos` (mais de 500 chamadas, sem resposta final) com `gpt-4o-mini`: o incidente 1 ficou `aberto`. Falta um limite de chamadas por agente (`max_llm_calls`) e levar o caso a `aguardando_decisao_manual`. Sem isso, T8.5, T11.3 e T11.5 não rodam de ponta a ponta.

### Ações imediatas do Felipe

- Recriar o banco local: `docker compose down -v && docker compose up -d --build` (o container atual foi criado antes da `010_conhecimento.sql` e não tem as tabelas do schema `conhecimento`).
- Preencher `OPENAI_API_KEY` no `.env` (hoje vazia) ou rodar o `agents` com `DEMO_LLM=fake`.
- Pedir ao Edison: (1) ok para fechar o contrato 1; (2) corrigir `monitor/tests/test_ciclo.py` (os 3 testes `test_pg_*` inserem `erp.talhoes (nome, lat, lon)` sem as colunas obrigatórias do `001_erp.sql`); (3) fixar a versão do `uv` nos dois Dockerfiles dele.
- Avisar o grupo (no PR): polling único por token do Telegram; contrato 16 (chaves do MCP) e 17 (variáveis do Edison).
