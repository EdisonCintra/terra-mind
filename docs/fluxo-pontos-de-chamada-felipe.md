# TerraMind · Pontos em que o código do Felipe é chamado (T1.1)

Base: "Arquitetura para desenvolvimento" (revisada em 29/10), `docs/contratos.md` (A1–A7, D1–D4, contratos 1–16) e o plano `2026-10-29-fazenda-canal-infra-felipe.md`.

Escopo atual: só defensivos agrícolas (D2, fechado); ambiente único na raiz (D3, fechado); decisão só por RAG (D1); Avaliador é LLM (A4).

## 1. Grafo do incidente e a sua parte

```
START → contexto ─┬─ sem_os ──────────────────────────────→ encerrar
                  └─ com_os → avaliador ─┬─ falhou ────────→ decisao_manual
                                         ├─ sem_violacao ──→ encerrar
                                         └─ violacao → acao_preventiva
acao_preventiva → agronomico  ─┐
acao_preventiva → operacional ─┴→ join → planejar (Supervisor ↔ Validador, até 2 voltas)
planejar ─┬─ reprovado → decisao_manual
          └─ valido → aprovacao (RequestInput "aprovacao:{plano_id}") → rota_resposta
rota_resposta ─┬─ SIM → executor ─┬─ ok → encerrar
               │                  └─ revalidar → planejar
               ├─ OPCOES (até 2x) → planejar
               └─ NAO → decisao_manual
```

Nós em que **o seu código é chamado** (marcados com ★):

| Nó | Dono | Chama o seu código? | O que chama | Contrato |
|---|---|---|---|---|
| Monitor | Edison | Indireto: INSERT em `agente.incidentes` | Tabela que você cria (`002_agente.sql`) | 3 |
| `contexto` | Gustavo | ★ MCP leitura | `listar_os` | 4, 5 |
| `avaliador` | Gustavo | Não (usa Qdrant) | Nada seu | 10 |
| `acao_preventiva` | Gustavo | ★ MCP escrita | `suspender_os` (única sem plano aprovado) | 5 |
| `agronomico` | Gustavo | ★ MCP leitura | `consultar_talhao` | 5 |
| `operacional` | Gustavo | ★ MCP leitura | `listar_os`, `disponibilidade_maquinas`, `listar_operadores` | 5 |
| `planejar` (Supervisor) | Gustavo | Não direto | Nada seu | |
| `validador` | Gustavo | ★ MCP leitura | `disponibilidade_maquinas`, `listar_os` | 5 |
| `aprovacao` | Gustavo | ★ Canal | `pedir_aprovacao` | 6, 7 |
| `rota_resposta` | Gustavo | Recebe de você | `ao_responder(Resposta)` (Telegram) ou `POST /incidentes/{id}/resposta` (painel) | 6, 7 |
| `executor` | Gustavo | ★ MCP escrita + canal | `reagendar_os`, `alocar_maquina`, `reservar_estoque`; `notificar_operador` | 5, 6 |
| `decisao_manual` | Gustavo | ★ Canal | `enviar_alerta` | A5 |
| Escalonamento (timeout) | Gustavo | ★ Canal | `enviar_alerta` | 6 |
| Painel "Simular chuva" | Você → Edison | Você chama | `POST {MONITOR_URL}/replay` | 14 |

`janelas_validas` **não** é seu (D4): fica em `agents/rules/`, fora do MCP.

## 2. Detalhe por ponto de chamada

### 2.1 `contexto` → MCP `listar_os`
- Entrada: janela de risco do incidente (`inicio`, `fim`) e `talhao_id`.
- Saída: lista de dicts (ou `{"ok": true, "os": [...]}` — ambos aceitos por `ErpMcp`) com as chaves do contrato 16 (`id`, `talhao_id`, `status`, `tipo_operacao`, `insumo_id`, `produto`, `tipo_insumo`, `dose_por_ha`, `unidade`, `preco_unitario`, `inicio_previsto`, `duracao_horas`, `maquina_id`, `maquina_tipo`, `operador_id`, `talhao`, `area_ha`).
- Sem OS na janela, o fluxo encerra.

### 2.2 `acao_preventiva` → MCP `suspender_os(os_id, incidente_id, motivo)`
- Só roda quando o Avaliador citou violação (trecho, fonte e página). Sem evidência ou com falha do Avaliador, **não é chamado** (A5).
- `UPDATE ... WHERE status = 'agendada'` e linha em `agente.auditoria` na mesma transação.
- A segunda chamada devolve `{"ok": false}`.
- O painel mostra a OS amarela e a citação na linha do tempo.

### 2.3 `agronomico` e `operacional` (ramos paralelos) → MCP leitura
- `consultar_talhao`, `listar_os`, `disponibilidade_maquinas` (sobreposição com `duracao_horas`), `listar_operadores`.
- Só leitura; precisam de respostas rápidas porque rodam em paralelo.

### 2.4 `validador` → MCP leitura
- Confere se a máquina está livre na janela proposta. Usa `disponibilidade_maquinas`.
- A janela contra o limite citado é checada pelo Gustavo, com `janelas_validas`.

### 2.5 `aprovacao` → `NotificationChannel.pedir_aprovacao(incidente_id, plano_id, texto)`
- O `agents` cria o plano em `agente.planos` antes de chamar (`criado_em` preenchido).
- Telegram: botões SIM / NÃO / Ver opções, `callback_data` curto (`r:{incidente_id}:{plano_id}:{resposta}`).
- Painel (`CHANNEL=painel`): só lê o plano pendente e mostra os botões.

### 2.6 Resposta do gerente → `agents`
- Telegram: `ao_responder(Resposta(incidente_id, plano_id, resposta, respondido_por))`, dentro do processo `agents`.
- Painel: `POST {AGENTS_URL}/incidentes/{id}/resposta` com `{plano_id, resposta, respondido_por}`.
- Resposta repetida para o mesmo plano → **409**. O painel e o Telegram mostram "decisão já registrada", sem erro.
- Quem retoma a sessão é o `agents` (`FunctionResponse` de id `aprovacao:{plano_id}`). Esse é o maior risco técnico e é testado no dia 2 (T8.5).

### 2.7 `executor` → MCP escrita + canal
- Revalida a janela e chama `reagendar_os`, `alocar_maquina`, `reservar_estoque`, sempre com `plano_id`.
- O MCP recusa se o plano não estiver aprovado (`aprovado_por` e `respondido_em` preenchidos), se a máquina estiver ocupada, se o estoque for insuficiente ou se o reagendamento já tiver sido feito (SIM duplo ou tardio).
- Depois: `notificar_operador(chat_id, texto)`.

### 2.8 `decisao_manual` e timeout → `enviar_alerta`
- Casos: Avaliador falhou, sem evidência na bula, Validador reprovou 2 vezes, NÃO, opções esgotadas (A5).
- Timeout (`APPROVAL_TIMEOUT_MIN`): alerta escalonado **uma vez**; `escalado_em` é gravado pelo `agents`. Um SIM tardio vale se a janela for revalidada.
- O painel mostra o selo "escalado" e alerta vermelho para `aguardando_decisao_manual`. Com `PainelChannel`, o alerta vai para `agente.auditoria`.

## 3. O que você entrega a cada ponto

| Ponto | Entrega | Tarefas | Dia |
|---|---|---|---|
| Leitura (contexto, agronômico, operacional, validador) | `erp_mcp` com 6 ferramentas de leitura, sem `janelas_validas` | T7.1–T7.4 | 1 |
| `acao_preventiva` | `suspender_os` | T7.5 | 2 |
| `executor` | `reagendar_os`, `alocar_maquina`, `reservar_estoque` + `pytest` | T7.6–T7.8 | 2 |
| `aprovacao`, `decisao_manual`, escalonamento, `executor` | `NotificationChannel`, `TelegramChannel`, `iniciar()`, `criar_canal` | T8.2–T8.7 | 2 |
| Fallback do canal | `PainelChannel` e telas do painel | T8.8, T8.9, T9.* | 3–4 |
| Monitor | `agente.incidentes` com `CHECK` de status e índice único parcial por talhão | T6.2, T6.6 | 1 |

## 4. Quem escreve em `agente.*`

| Coluna ou tabela | Quem escreve |
|---|---|
| `incidentes` (criação) | Monitor (Edison) |
| `incidentes.status` (depois) | só `agents` (Gustavo) |
| `planos` (criação, `criado_em`, `escalado_em`, `aprovado_por`, `respondido_em`) | `agents` (Gustavo) |
| `auditoria` | MCP (nas escritas) e `PainelChannel` (alertas) |
| Sessões do ADK | `DatabaseSessionService` (`search_path=agente`) |

## 5. Pontos validados contra `origin/feat/agents` (30/10)

- Chaves de `listar_os` e retorno do MCP fechados no contrato 16 (`id` em vez de `os_id`; escritas `{"ok": bool, ...}`; leituras aceitam lista/dict direto ou `{"ok": true, ...}`).
- `iniciar()` e `parar()` são chamados no `lifespan` do FastAPI de `agents` (`agents/api.py`), e `criar_canal(nome, ao_responder)` vem de `channels.base`.
- `PainelChannel` grava só o alerta; o plano é criado e atualizado exclusivamente por `agents`.
- `janelas_validas` não precisa de ferramenta extra no MCP (`disponibilidade_maquinas` já cobre o Validador e o Operacional).
- D2 e D3 fechados (Gustavo alinhará `origin/feat/agents` no rebase com `main`). Falta apenas escolher o defensivo da demo com o Edison (contrato 1).
