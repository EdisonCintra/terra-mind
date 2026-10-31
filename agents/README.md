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
| Resposta | SIM / NÃO / Ver opções | SIM → Executor; Ver opções → novo plano (até 2); NÃO → decisão manual | — |
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
               └─ NAO → decisao_manual
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
| `TELEGRAM_BOT_TOKEN`, `GERENTE_CHAT_ID` | — | Lidas pelo `channels` do Felipe |
| `APPROVAL_TIMEOUT_MIN` | `30` | Escalonamento de plano sem resposta (uma vez) |
| `MAX_REPLANEJAMENTOS`, `MAX_VOLTAS_VALIDADOR` | `2`, `2` | Limites do fluxo |
| `TIMEOUT_LLM_S`, `TENTATIVAS_LLM` | `90`, `2` | Timeout e novas tentativas de cada chamada à OpenAI (no cliente LiteLLM) |

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
