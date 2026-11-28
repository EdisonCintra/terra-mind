# agents — workflow do incidente (frente do Gustavo)

Recebe o incidente aberto pelo Monitor, avalia o risco das OS na janela, suspende o que viola, monta e valida um plano de reagendamento, pede aprovação ao gerente, retoma a sessão quando a resposta chega (mesmo depois de um restart) e executa o plano no ERP.

Plano: [docs/plans/2026-10-29-agentes-gustavo.md](../docs/plans/2026-10-29-agentes-gustavo.md) · Contratos: [docs/contratos.md](../docs/contratos.md)

## Inputs, lógica e outputs

| Etapa | Input | Lógica | Output |
| --- | --- | --- | --- |
| Contexto | `agente.incidentes` (gatilhos + previsão de 120 h); OS via MCP | OS `agendada` do talhão que começam até 24 h antes da janela de risco e até o fim dela | `os_em_risco` |
| Avaliação | OS + resumo da previsão em blocos de 6 h | **LLM** (`avaliador_risco`) busca trechos com `buscar_trechos` e decide; schema exige trecho, fonte e página em toda violação | `avaliacao` |
| Suspensão | Violações | `suspender_os` (única escrita sem aprovação) + `calcular_impacto` em código | `os_suspensas`, `impactos` (R$) |
| Análises | OS suspensas | Agronômico e Operacional (**LLMs**) em paralelo; falha de um vira texto e o fluxo segue | `analise_agronomica`, `analise_operacional` |
| Plano | Análises, janelas válidas | Supervisor (**LLM**) propõe; Validador (código) confere clima, horário, horizonte e máquina; até 2 voltas | `agente.planos` + mensagem com rodapé montado em código |
| Aprovação | Plano | `RequestInput(interrupt_id="aprovacao:{plano_id}")` pausa a sessão | Telegram ou painel |
| Resposta | Aceitar Proposta (`SIM`) / Ver próxima opção (`OPCOES`) / Manter data original (`NAO`) | Aceitar → Executor; Ver próxima opção → novo plano (até 2; esgotadas, encerra com as OS suspensas, que o gerente remarca ou cancela no painel); Manter data original → `retomar_os` volta cada OS a `agendada` no horário de antes (o gerente assume o risco; auditado) e encerra | — |
| Execução | SIM | Revalida a janela; `reagendar_os`, `alocar_maquina`, `reservar_estoque`; avisa o operador | ERP atualizado, incidente `encerrado` |

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
               ├─ NAO (manter data original) → manter_data ─┬─ ok → encerrar (OS de volta a agendada)
               │                                            └─ falhou → decisao_manual
               └─ (opções esgotadas) → encerrar (OS seguem suspensas; remarcar ou cancelar no painel)
```

Garantias, cada uma com teste:

- **Valores em R$ só saem de código.** O rodapé da mensagem (perda, data, máquina, fonte e página) é montado com o estado da sessão, não com o texto do LLM (`test_rodape_usa_numeros_do_codigo_e_nao_do_llm`).
- **Retomada depois de restart.** A sessão fica em Postgres, no schema `agente`. Um orquestrador novo retoma do nó de aprovação (`test_retoma_sessao_em_orquestrador_novo`).
- **Clique duplo não age duas vezes.** A resposta repetida devolve 409 e não reexecuta o ERP (`test_resposta_repetida_nao_reexecuta`).
- **Falha do Avaliador não suspende.** Se o LLM cair ou a saída não tiver citação, a OS segue agendada e o gerente recebe um alerta (decisão A5).

## Endpoints (porta 8000)

| Método | Rota | Corpo | Respostas |
| --- | --- | --- | --- |
| GET | `/health` | — | 200 `{"status": "ok"}` |
| POST | `/incidentes/{id}/iniciar` | — | 202 (roda em segundo plano, idempotente) · 404 incidente inexistente |
| POST | `/incidentes/{id}/resposta` | `{"plano_id", "resposta": "SIM"\|"NAO"\|"OPCOES", "respondido_por"}` | 200 · 409 repetida · 404 plano desconhecido · 422 |

O Telegram não passa pela API: o `TelegramChannel` do Felipe roda neste processo e chama `Orquestrador.responder` (contrato 7).

## Rodar

Tudo da raiz do monorepo, no ambiente único (D3):

```bash
uv sync
uv run pytest agents                          # 84 testes; os de Postgres sobem um container (Docker)
uv run python -m agents.verificar_modelos     # confere tools + output_schema de cada MODEL_* na OpenAI
uv run uvicorn agents.api:app --port 8000     # lê o .env da raiz, agents/.env e as variáveis do ambiente
docker compose up -d --build agents           # o serviço no compose, com o canal do Felipe dentro
```

No Windows, defina `PYTHONUTF8=1` (exigência do LiteLLM).

### Agentes no `adk web` (sem Postgres, MCP nem Telegram)

Uma pasta por agente, no padrão do ADK (https://adk.dev/get-started/python/): cada uma tem `__init__.py` e `agent.py`, que define `criar(deps)` (usado pelo workflow do serviço) e exporta `root_agent` para o `adk web`.

```
agents/agentes/
├── _comum.py          # regras gerais e helpers das instruções
├── _demo.py           # fazenda simulada para o adk web
├── avaliador_risco/   # __init__.py + agent.py
├── agronomico/
├── operacional/
├── supervisor/
└── terramind/         # o workflow inteiro (app com retomada)
```

```powershell
$env:PYTHONUTF8 = "1"
uv run adk web agents/agentes                 # abra http://localhost:8000 e escolha o agente no canto superior esquerdo
uv run adk run agents/agentes/terramind       # o fluxo inteiro, no terminal
uv run adk run agents/agentes/supervisor      # um agente sozinho
```

- **terramind:** digite um número de incidente, por exemplo `1`. Cada número novo recria a fazenda simulada: 80 mm de chuva nas próximas 36 h e a pulverização de Abacus HC (fungicida, seed do Felipe) daqui a 20 h.
- Um agente sozinho começa com o estado do cenário de chuva já preenchido (OS suspensa, avaliação, análises); qualquer mensagem dispara a tarefa.
- Na pausa do `terramind`, responda com `{"resposta": "SIM", "respondido_por": "gustavo"}` (ou `"NAO"`, `"OPCOES"`).
- Com `OPENAI_API_KEY` definida, usa os modelos `MODEL_*` de verdade; serve para ensaiar os prompts. Sem chave, ou com `$env:DEMO_LLM = "fake"`, usa LLMs roteirizados.
- Alertas, pedidos de aprovação e avisos ao operador aparecem no log do terminal.

| Variável | Padrão | Uso |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://terramind:terramind@localhost:5432/terramind` | `agente.*` e sessões do ADK (vira `postgresql+asyncpg`, `search_path=agente`) |
| `ERP_MCP_URL` | `http://localhost:8030/mcp` | MCP do Felipe |
| `CONHECIMENTO_URL` | `http://localhost:8010` | API do Edison |
| `OPENAI_API_KEY` | — | Chave da API da OpenAI |
| `MODEL_AVALIADOR`, `MODEL_AGRONOMICO`, `MODEL_OPERACIONAL`, `MODEL_SUPERVISOR` | `openai/gpt-6-luna` | Formato `openai/<modelo>`; trocar um agente por vez se precisar de mais qualidade |
| `REASONING_EFFORT` | `low` | Raciocínio dos quatro agentes. O `gpt-6-luna` não aceita `minimal`; `none` desliga. Vazio = padrão do modelo |
| `CHANNEL` | `painel` | `telegram` ou `painel` |
| `TELEGRAM_BOT_TOKEN`, `GERENTE_CHAT_ID` | — | Lidas pelo `channels` do Felipe (o gerente de cada fazenda vem de `erp.gerentes`; `GERENTE_CHAT_ID` é o padrão) |
| `APPROVAL_TIMEOUT_MIN` | `30` | Escalonamento de plano sem resposta (uma vez) |
| `MAX_REPLANEJAMENTOS`, `MAX_VOLTAS_VALIDADOR` | `2`, `2` | Limites do fluxo |
| `TIMEOUT_LLM_S`, `TENTATIVAS_LLM` | `90`, `2` | Timeout e novas tentativas de cada chamada à OpenAI (no cliente LiteLLM) |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | — | Liga a observabilidade; sem as duas, nada é exportado |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Região do Langfuse |
| `LANGFUSE_TRACING_ENVIRONMENT` | `development` | Separa os traces de dev, staging e produção |
| `LANGFUSE_RELEASE`, `LANGFUSE_SAMPLE_RATE`, `LANGFUSE_TRACING_ENABLED` | —, `1.0`, `true` | Versão do deploy, amostragem e chave geral |

## Assistente do chat (Telegram, WhatsApp e terminal)

Texto livre nos canais vai para o [`assistente.py`](assistente.py), um agente que responde dúvidas com o que o painel mostra. As ferramentas são só de leitura ([`clientes/consultas.py`](clientes/consultas.py)):

| Ferramenta | O que traz |
| --- | --- |
| `listar_talhoes` | talhões, cultura, estágio, área |
| `listar_ordens`, `detalhar_ordem` | OS por status, período e talhão; perdas e execução |
| `listar_incidentes`, `detalhar_incidente` | incidentes, planos (mensagem e resposta), perdas e linha do tempo |
| `planos_pendentes` | planos esperando o gerente |
| `perda_evitada` | R$ por situação desde o início da safra |
| `estoque_insumos`, `listar_maquinas` | estoque e o comprometido; máquinas e a próxima OS |
| `previsao_talhao` | previsão do Monitor em blocos de 6 h |
| `janelas_de_aplicacao` | faixas boas para aplicar (a conta da Agenda, G2) |
| `perguntar_a_bula` | o Pergunte à bula, com a citação conferida em código |

- **Escopo por fazenda (contrato 20).** O canal manda o contato (`telegram:<chat>`, `whatsapp:<telefone>`, `terminal`). Gerente em `erp.gerentes` vê só as fazendas dele; o terminal vê todas; os chats extras (`TELEGRAM_CHATS_BULA`, `WHATSAPP_NUMEROS_BULA`) só consultam a bula.
- **Só consulta.** Responder plano continua pelos botões (ou `sim`/`opcoes`/`nao`); editar OS, pelo painel.
- **Conversa.** Cada contato tem uma conversa de 30 min sem mensagem, para perguntas de seguimento ("e a OS 2?").
- `MODEL_ASSISTENTE` escolhe o modelo.

### Guardrails (NeMo Guardrails)

[`guardrails.py`](guardrails.py) protege o chat contra prompt injection, jailbreak e uso fora do assunto, com duas barreiras `self check` do NeMo (política em português, no próprio módulo):

| Barreira | Quando | Bloqueia |
| --- | --- | --- |
| Tamanho (código) | antes de tudo | mensagem com mais de 1000 caracteres, sem gastar LLM |
| `self check input` | antes do agente | mudar ou revelar as instruções, trocar de papel (DAN, "modo desenvolvedor"), delimitadores falsos de sistema, texto codificado, dados de outra fazenda ou de outras pessoas, segredos, conteúdo perigoso e assunto fora da fazenda |
| `self check output` | antes de mandar a resposta | vazamento de instruções ou segredos, obediência a instrução escondida nos dados (injeção indireta), troca de papel, links e comandos |

- Mensagem barrada na entrada não chega ao agente nem às ferramentas. Resposta barrada na saída descarta a conversa (o histórico pode estar contaminado).
- **Falha fechada:** se o guardrail não rodar (LLM fora do ar, NeMo não subiu), a mensagem é recusada.
- O prompt do assistente também trata o que vem das ferramentas como dado, nunca como instrução.
- No Langfuse: etapas `guardrail-entrada` e `guardrail-saida` (tipo `guardrail`) e o score `guardrail` (`permitida`, `bloqueada_entrada`, `bloqueada_saida`, `muito_longa`, `erro`).
- A telemetria do NeMo para a NVIDIA fica desligada (`NEMO_GUARDRAILS_NO_USAGE_STATS=1`).
- `uv run python -m agents.avaliar_guardrails` roda os casos de [`avaliacao/guardrails.yaml`](avaliacao/guardrails.yaml) contra o LLM real (legítimos, injeção direta, dados de terceiros, fora do assunto e saídas maliciosas).

## Observabilidade (Langfuse)

[`observabilidade.py`](observabilidade.py) liga o Langfuse no startup da API (e no `avaliar_bula`) e faz o flush no desligamento. O ADK é instrumentado pelo OpenInference (`GoogleADKInstrumentor`) sobre OpenTelemetry: cada nó do grafo vira um span `CHAIN`, cada agente um `AGENT`, cada chamada ao LLM uma `GENERATION` (modelo, tokens, custo e raciocínio) e cada ferramenta um `TOOL`, aninhados como rodaram. Nenhuma função do módulo levanta exceção: se o Langfuse cair, o fluxo segue igual.

**Traces** (um por caso de uso; os nomes são contrato com dashboards e avaliadores, não mude sem migrar):

| Trace | Quando | Sessão / usuário | Saída |
| --- | --- | --- | --- |
| `avaliar-incidente` | `POST /incidentes/{id}/iniciar`, do START até a pausa ou o fim | `incidente-<id>` / `terramind` | status, motivo, OS em risco e suspensas, plano, mensagem ao gerente |
| `retomar-incidente` | Resposta do gerente (painel ou Telegram) | `incidente-<id>` / quem respondeu | idem |
| `perguntar-bula` | `POST /bula/perguntar` (painel). Dentro do chat, vira etapa do `responder-chat` | — / `painel` | resposta conferida |
| `extrair-limites` | `POST /limites/extrair` (contrato 26) | — / `terramind` | limites aceitos e descartados |
| `responder-chat` | Pergunta livre no Telegram, WhatsApp ou terminal (assistente) | `chat-<id>` (uma por conversa) / quem perguntou | a resposta enviada |

A sessão do Langfuse é a mesma do ADK: a avaliação e cada retomada de um incidente aparecem em sequência na visão de sessão. Todos os spans levam as tags `agents` + o caso de uso e os metadados (`incidente_id`, `plano_id`, `produto`), então os filtros acham também as generations.

**Scores** (o que o código confere depois do LLM; base dos dashboards de qualidade):

| Score | Tipo | Onde |
| --- | --- | --- |
| `desfecho-incidente` | categórico | status do incidente ao fim de cada execução |
| `decisao-gerente` | categórico (`SIM`/`NAO`/`OPCOES`) | feedback humano, no trace da retomada |
| `avaliacao-risco` | categórico (`violacao`/`sem_violacao`/`falhou`) | rota depois do Avaliador |
| `violacoes-descartadas` | numérico | violações que os próprios números contradizem |
| `plano-validado` | booleano | cada volta do Validador, com os motivos da reprovação |
| `falha-agente` | categórico (nome do agente) | erro do LLM, saída fora do schema ou loop de ferramentas |
| `bula-encontrou`, `citacoes-descartadas` | booleano, numérico | citação inventada pelo LLM e barrada em código |
| `limites-aceitos`, `limites-descartados` | numérico | conferência dos limites lidos da bula |

Falhas que as proteções dos agentes engolem (para não derrubar o workflow) ficam também como `ERROR`/`WARNING` no span onde aconteceram, com o motivo em `statusMessage`.

## No `docker-compose.yml`

O serviço `agents` está no compose da raiz, com contexto de build na raiz (D3). A imagem leva `agents`,
`channels` e `erp_mcp`, porque o canal do Felipe (`CHANNEL=telegram` ou `painel`) roda dentro deste
processo (contrato 7).

## O que este serviço espera das outras frentes

- **MCP do Felipe (contrato 5):**
  - `listar_os` devolve, por OS, `id`, `talhao_id`, `status`, `tipo_operacao`, `insumo_id`, `produto`, `tipo_insumo` (ou `tipo`), `dose_por_ha`, `unidade`, `preco_unitario`, `inicio_previsto`, `duracao_horas`, `maquina_id`, `operador_id`, `talhao` e `area_ha`;
  - `consultar_talhao` devolve `nome`, `cultura`, `estagio_fenologico` e `area_ha`;
  - `disponibilidade_maquinas` devolve máquinas com `id`;
  - `listar_operadores` devolve `id` e `telegram_chat_id`;
  - as escritas devolvem `{"ok": ...}`. Exceção no servidor também vira `{"ok": false}` aqui.
- **Conhecimento do Edison (contratos 10 e 11):**
  - `POST /trechos/busca` devolve uma lista de `{texto, fonte_doc, pagina, secao, produto}`;
  - `GET /fatores-perda` devolve `{fator, fonte_ou_estimativa}` ou 404.
- **Monitor do Edison (contrato 3):** o `previsao_snapshot` traz as 120 h da previsão, e `gatilho` é a lista de `Gatilho`.
