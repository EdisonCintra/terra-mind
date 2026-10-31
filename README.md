# TerraMind

Agente que protege operações agrícolas agendadas contra eventos climáticos extremos: o Monitor detecta a chuva prevista, a ordem de serviço em risco é suspensa e um plano de reagendamento vai para aprovação do gerente.

Monorepo com um serviço por pasta. Planos de implementação em [docs/plans/](docs/plans/).

Contratos entre as frentes: [docs/contratos.md](docs/contratos.md).

## Agentes e integração (frente do Gustavo)

Serviço **`agents`** (porta 8000): o workflow ADK do incidente, do contexto à execução do plano aprovado. Detalhes, comandos e variáveis em [agents/README.md](agents/README.md).

## Conhecimento e clima (frente do Edison)

Dois serviços: **`conhecimento`** (pasta `base_agricola/`, porta 8010), que guarda os trechos das bulas no Qdrant e os fatores de perda no Postgres, e **`monitor`** (porta 8020), que compara a previsão do tempo com limiares e abre incidentes.

| Etapa | Input | Lógica | Output |
|---|---|---|---|
| Base de conhecimento (offline) | Bulas em PDF (`base_agricola/dados/docs/`) listadas em `fontes.yaml` | Texto por página → trechos de até 500 caracteres por seção da bula, sem cruzar página → embeddings no Qdrant | Trechos com `produto`, `secao` e `pagina` para citar |
| Busca (`buscar_trechos`) | Consulta + nome do produto | Busca semântica filtrada por produto | Trechos com fonte e página; lista vazia = a bula não traz o limite |
| Fatores de perda | `fatores_perda.yaml` | Cada fator com página citada ou estimativa declarada | `GET /fatores-perda?tipo_insumo=&condicao=` |
| 1 · Gatilho (Monitor) | Previsão horária de 5 dias (Open-Meteo ou cenário gravado) | Chuva acumulada, vento e umidade na janela de 48 h contra limiares | Incidente em `agente.incidentes` com as 120 h da previsão; aviso em `POST {AGENTS_URL}/incidentes/{id}/iniciar` |

Bulas da demo, todas com limite de chuva escrito: Abacus HC (p. 7), Ativum (p. 6), Brucia (p. 9) e Amplexus (p. 5).

### Comandos

```bash
uv sync                                                         # ambiente único do monorepo
uv run pytest base_agricola agents monitor                      # testes rápidos
RODAR_LENTOS=1 uv run pytest base_agricola -k aceitacao         # bulas reais + modelo de embeddings
docker compose exec conhecimento python -m base_agricola.ingest indexar          # PDFs → Qdrant
docker compose exec conhecimento python -m base_agricola.ingest carregar-fatores # YAML → Postgres
curl -X POST localhost:8020/replay -H 'content-type: application/json' -d '{"cenario":"chuva_80mm.json"}'
```

No Windows, mande corpos com acento a partir de um arquivo UTF-8 (`--data-binary @arquivo.json`): o terminal pode enviar outra codificação e a API responde 400.

### Blocos para o `docker-compose.yml`

```yaml
  conhecimento:
    build: { context: ., dockerfile: base_agricola/Dockerfile }
    environment:
      DATABASE_URL: postgresql+psycopg://terramind:terramind@postgres:5432/terramind
      QDRANT_URL: http://qdrant:6333
    ports: ["8010:8010"]
    depends_on: { postgres: { condition: service_healthy }, qdrant: { condition: service_started } }

  monitor:
    build: { context: ., dockerfile: monitor/Dockerfile }
    environment:
      DATABASE_URL: postgresql+psycopg://terramind:terramind@postgres:5432/terramind
      AGENTS_URL: http://agents:8000
      MONITOR_MODE: ${MONITOR_MODE:-replay}
    ports: ["8020:8020"]
    depends_on: { postgres: { condition: service_healthy } }
```

O `DATABASE_URL` destes serviços usa o driver `postgresql+psycopg://` (SQLAlchemy).

## Fazenda simulada, canal e painel (frente do Felipe)

Quatro peças: o **mini-ERP** (Postgres), o **servidor MCP** que os agentes usam para ler e escrever nele, o **canal** de notificação (Telegram ou painel) e o **painel web**.

| Peça | Onde | O que faz |
|---|---|---|
| Mini-ERP | `db/migrations/` (`001_erp.sql`, `002_agente.sql`, `900_seed.sql`) | Talhões, insumos, máquinas, operadores, ordens de serviço e leituras de solo; no schema `agente`, incidentes, planos e auditoria. O seed usa datas relativas a `now()` e só defensivos (Abacus HC e Engeo Pleno S). |
| Servidor MCP | `erp_mcp/` (porta 8030, `/mcp`) | 6 ferramentas de leitura e 4 de escrita. Só `suspender_os` age sem aprovação (`agendada → suspensa_preventiva`); `reagendar_os`, `alocar_maquina` e `reservar_estoque` exigem plano aprovado no banco. Toda escrita grava `agente.auditoria` na mesma transação. |
| Canal | `channels/` | `NotificationChannel` com `TelegramChannel` (botões SIM / NÃO / Ver opções, long polling) e `PainelChannel` (grava o alerta na auditoria). `CHANNEL=telegram` ou `painel` escolhe um sem mexer no fluxo. Roda dentro do processo `agents`. |
| Painel web | `painel/` (porta 8080) | Quatro telas: ordens de serviço ao vivo, linha do tempo com a citação (fonte e página), aprovação pendente e "Simular chuva". Sem login e sem CDN. |

Inputs, lógica e outputs do que é seu no fluxo:

| Etapa | Input | Lógica | Output |
|---|---|---|---|
| Suspensão | `os_id`, `incidente_id`, motivo com a citação | `UPDATE ... WHERE status = 'agendada'` e auditoria na mesma transação | OS `suspensa_preventiva`; a segunda chamada devolve `{"ok": false}` |
| Aprovação | Plano em `agente.planos` | Mensagem com botões no Telegram, ou os mesmos botões no painel | `Resposta(incidente_id, plano_id, SIM / NAO / OPCOES, respondido_por)` para o `agents` |
| Execução | `plano_id` aprovado | O MCP recusa se o plano não está aprovado, se a máquina está ocupada, se falta estoque ou se já foi feito | OS reagendada, máquina alocada, estoque baixado |

### Como subir

```bash
cp .env.example .env     # preencha TELEGRAM_BOT_TOKEN, GERENTE_CHAT_ID e OPENAI_API_KEY
docker compose up -d --build
```

- Painel em `http://localhost:8080`. O botão "Simular chuva" dispara o cenário `chuva_80mm.json` no Monitor.
- As migrations só rodam quando o banco é criado. Se mudar um `.sql`, recrie: `docker compose down -v && docker compose up -d --build` (o `-v` apaga os dados).
- Se a porta 5432 do host estiver ocupada (Postgres do Windows), suba com `POSTGRES_PORT=5433` e use `DATABASE_URL=postgresql://terramind:terramind@localhost:5433/terramind` ao rodar testes fora do Docker.
- Telegram: crie o bot no @BotFather e ponha o token e o `chat_id` no `.env`. Só **um** processo pode fazer polling por token (erro `Conflict`); use `CHANNEL=painel` nos demais ambientes.
- Teste manual do bot: `uv run python -m channels.teste_bot`.

```bash
uv run pytest tests -q      # MCP, migrations, canais e painel (precisa do Postgres do compose)
uv run ruff check .
```
