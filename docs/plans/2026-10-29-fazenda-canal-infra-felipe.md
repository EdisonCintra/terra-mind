# TerraMind · Fazenda simulada, canal e infra (frente do Felipe)

> Plano de implementação escrito para quem vem de CRUD. Cada parte diz **o que fazer**, **por que** e **como saber que deu certo**. Os termos novos estão em **negrito** na primeira vez que aparecem e explicados no [Glossário](#glossário) no fim. Leia o glossário antes de começar; são 10 minutos que poupam horas.

**Spec de referência:** "Arquitetura para desenvolvimento" (27/10/2026).

---

## 1. O TerraMind em um parágrafo

Uma fazenda tem **ordens de serviço** (OS) agendadas, por exemplo "pulverizar fungicida no talhão 3 amanhã às 7h". O defensivo precisa de algumas horas sem chuva para agir; se chover antes, a chuva lava o produto da planta e o dinheiro é perdido. O TerraMind vigia a previsão do tempo; quando vê chuva forte chegando, **suspende a OS sozinho** (é reversível, então pode fazer sem perguntar), monta um plano para remarcá-la e **pede aprovação ao gerente no Telegram**. Se o gerente responder SIM, o sistema remarca a OS, aloca a máquina e avisa o operador.

## 2. O seu pedaço

Você é dono da **fazenda de mentira** e de **como o sistema conversa com as pessoas**. Na prática são quatro peças, e todas se parecem mais com CRUD do que parece:

| Peça | O que é, sem jargão | Parecido com |
|---|---|---|
| **Mini-ERP** (Postgres) | As tabelas da fazenda: talhões, insumos, máquinas, operadores, ordens de serviço | O banco de qualquer CRUD |
| **Servidor MCP do ERP** | Um conjunto de funções (listar OS, suspender OS...) que os agentes de IA podem chamar | Uma API REST, só que o "cliente" é uma IA |
| **Canal (Telegram + painel)** | Mandar mensagem com botões SIM/NÃO e receber o clique | Um formulário com botão de enviar |
| **Painel web + docker-compose** | Uma tela que mostra as OS mudando de status, e o arquivo que sobe tudo junto | A tela de listagem de um CRUD |

Quem chama o seu código:

```
                 ┌───────────────────── Edison ─────────────────────┐
                 │  monitor (clima)  →  abre incidente               │
                 └───────────────────────────┬──────────────────────┘
                                             ▼
┌──────────── Gustavo ────────────┐   ┌──────────── VOCÊ ─────────────┐
│ agents (ADK + agentes de IA)    │──▶│ erp-mcp   (ferramentas do ERP)│──▶ Postgres (schema erp)
│                                 │──▶│ channels  (Telegram / painel) │──▶ gerente e operadores
│ recebe a resposta do gerente  ◀─┼───│ painel    (tela + aprovação)  │
└─────────────────────────────────┘   │ docker-compose.yml            │
                                      └───────────────────────────────┘
```

**O que NÃO é seu:** os agentes de IA e a lógica de decisão (Gustavo), as regras das bulas e o monitor de clima (Edison). Você não precisa entender como a IA decide; precisa entregar funções que fazem exatamente o que o nome diz.

---

## 3. Ferramentas

| Ferramenta | Para quê | Instalação |
|---|---|---|
| Docker Desktop | Rodar Postgres e os serviços | docker.com |
| Python 3.12 + **uv** | Linguagem e gerenciador de pacotes | `pip install uv` ou docs do uv |
| `psycopg[binary]` | Falar com o Postgres usando SQL puro | `uv add "psycopg[binary]"` |
| `mcp` | Criar o servidor MCP | `uv add "mcp[cli]"` |
| `python-telegram-bot` | Bot do Telegram | `uv add python-telegram-bot` |
| `fastapi`, `uvicorn`, `jinja2` | Painel web | `uv add fastapi uvicorn jinja2` |
| `httpx` | Chamar outros serviços por HTTP | `uv add httpx` |
| DBeaver ou pgAdmin | Olhar o banco com interface gráfica | opcional |
| MCP Inspector | Testar o servidor MCP clicando | `npx @modelcontextprotocol/inspector` |

Usamos **SQL puro com psycopg** (sem ORM). Você escreve `SELECT`/`UPDATE` como já faz e vê exatamente o que acontece no banco.

---

## 4. Contratos para fechar no dia 1

**Contrato** = o combinado entre duas frentes sobre nomes, formatos e endereços. Se o contrato estiver fechado, cada um programa sozinho sem quebrar o outro. Leve esta tabela para a conversa com o Gustavo e o Edison e anote o que for decidido em `docs/contratos.md`.

| # | Assunto | Proposta | Com quem |
|---|---|---|---|
| 1 | Nome do produto | `erp.insumos.produto` tem o mesmo texto que o `produto` dos trechos no Qdrant (o Avaliador filtra por ele). O defensivo da demo ainda vai ser escolhido no Agrofit com o Edison | Edison |
| 2 | Migrations | Arquivos SQL numerados em `db/migrations/`: os seus são `001_erp.sql`, `002_agente.sql` e `900_seed.sql`; o do Edison é `010_conhecimento.sql` | Edison, Gustavo |
| 3 | Schema `agente` | Você cria as tabelas `incidentes`, `planos` e `auditoria` (seção 6.2); o Gustavo confere os campos | Gustavo |
| 4 | Endereço do MCP | `http://erp-mcp:8030/mcp`, transporte **streamable HTTP** | Gustavo |
| 5 | Nomes das ferramentas MCP | Lista da seção 7 | Gustavo |
| 6 | Resposta do gerente | O canal chama a função `ao_responder(resposta)` que o Gustavo passa para você; o painel chama `POST {AGENTS_URL}/incidentes/{id}/resposta` (formato na seção 8.3) | Gustavo |
| 7 | Portas | agents 8000 · conhecimento 8010 · monitor 8020 · erp-mcp 8030 · painel 8080 · postgres 5432 · qdrant 6333 | todos |
| 8 | Botão "Simular chuva" | O painel chama `POST {MONITOR_URL}/replay` com `{"cenario":"chuva_80mm.json"}` | Edison |
| 9 | `janelas_validas` | A spec põe essa ferramenta no MCP, mas ela precisa de previsão do tempo e de regras, que não são dados do ERP. Proposta: fica com o Gustavo ou o Edison | Gustavo, Edison |

---

## 5. Estrutura de pastas que você vai criar

```
terramind/
├── db/
│   └── migrations/
│       ├── 001_erp.sql          # tabelas da fazenda
│       ├── 002_agente.sql       # incidentes, planos, auditoria
│       └── 900_seed.sql         # dados de exemplo
├── erp_mcp/
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── erp_mcp/
│       ├── db.py                # conexão com o banco
│       ├── leitura.py           # ferramentas de consulta
│       ├── escrita.py           # ferramentas que alteram dados
│       └── server.py            # registra as ferramentas e sobe o servidor
├── channels/
│   ├── pyproject.toml
│   └── channels/
│       ├── base.py              # NotificationChannel (a "interface")
│       ├── telegram.py          # implementação Telegram
│       └── painel.py            # implementação painel (fallback)
├── painel/
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── painel/
│       ├── main.py              # FastAPI
│       └── templates/           # HTML (Jinja2)
├── docker-compose.yml
└── .env.example
```

---

## 6. Banco de dados

### 6.1 Como as migrations vão rodar (o jeito mais simples)

A imagem oficial do Postgres executa sozinha, em ordem alfabética, todo arquivo `.sql` que estiver na pasta `/docker-entrypoint-initdb.d`, **só na primeira vez que o banco é criado**. Então montamos `db/migrations/` nessa pasta e pronto: não há ferramenta de migration para aprender.

> ⚠️ **Armadilha clássica:** você mudou um `.sql` e nada aconteceu? É porque o banco já existia. Para recriar do zero: `docker compose down -v` (o `-v` apaga o **volume**, isto é, os dados) e depois `docker compose up -d postgres`.

### 6.2 `001_erp.sql`: a fazenda

```sql
CREATE SCHEMA IF NOT EXISTS erp;

CREATE TABLE erp.talhoes (
    id                  serial PRIMARY KEY,
    nome                text NOT NULL,
    area_ha             numeric(10,2) NOT NULL CHECK (area_ha > 0),
    cultura             text NOT NULL,              -- 'soja' | 'milho'
    data_plantio        date NOT NULL,
    estagio_fenologico  text NOT NULL,              -- ex.: 'V6', 'R1'
    lat                 numeric(9,6) NOT NULL,
    lon                 numeric(9,6) NOT NULL,
    tipo_solo           text
);

CREATE TABLE erp.insumos (
    id              serial PRIMARY KEY,
    produto         text NOT NULL UNIQUE,           -- contrato 1: igual ao do Edison
    tipo            text NOT NULL,                  -- 'fungicida' | 'herbicida' | 'inseticida' (docs/contratos.md, contrato 15)
    unidade         text NOT NULL,                  -- 'kg' | 'L'
    preco_unitario  numeric(12,2) NOT NULL,         -- R$ por unidade
    estoque         numeric(12,2) NOT NULL CHECK (estoque >= 0),
    bula_id         text                            -- id do documento na base do Edison
);

CREATE TABLE erp.maquinas (
    id      serial PRIMARY KEY,
    nome    text NOT NULL,
    tipo    text NOT NULL,                          -- 'pulverizador' | 'distribuidor' ...
    status  text NOT NULL DEFAULT 'disponivel'      -- 'disponivel' | 'manutencao'
);

CREATE TABLE erp.operadores (
    id                serial PRIMARY KEY,
    nome              text NOT NULL,
    telegram_chat_id  bigint
);

CREATE TABLE erp.ordens_servico (
    id               serial PRIMARY KEY,
    talhao_id        int NOT NULL REFERENCES erp.talhoes(id),
    tipo_operacao    text NOT NULL,                 -- 'adubacao' | 'pulverizacao' ...
    insumo_id        int REFERENCES erp.insumos(id),
    dose_por_ha      numeric(10,3),                 -- MESMA unidade de insumos.unidade
    inicio_previsto  timestamptz NOT NULL,
    duracao_horas    numeric(5,1) NOT NULL DEFAULT 8,
    maquina_id       int REFERENCES erp.maquinas(id),
    operador_id      int REFERENCES erp.operadores(id),
    status           text NOT NULL DEFAULT 'agendada'
        CHECK (status IN ('agendada','suspensa_preventiva','reagendada','executada','cancelada'))
);

CREATE TABLE erp.leituras_solo (
    talhao_id    int NOT NULL REFERENCES erp.talhoes(id),
    ts           timestamptz NOT NULL,
    umidade_pct  numeric(5,2) NOT NULL,
    PRIMARY KEY (talhao_id, ts)
);
```

Por que esses detalhes:
- **`dose_por_ha` na mesma unidade do insumo.** A perda é calculada como `area_ha × dose_por_ha × preco_unitario × fator`. Se a dose estiver em kg e o preço em R$/saco, o número sai errado no palco.
- **`CHECK` no status.** O banco recusa um status digitado errado (`"suspensa"` em vez de `"suspensa_preventiva"`). É uma proteção barata.
- **`duracao_horas`** não está na spec. Foi acrescentada porque, sem ela, não dá para saber se uma máquina está ocupada num horário. Avise o Gustavo.

### 6.3 `002_agente.sql`: registros do sistema (confirmar com o Gustavo)

```sql
CREATE SCHEMA IF NOT EXISTS agente;

CREATE TABLE agente.incidentes (
    id                 serial PRIMARY KEY,
    talhao_id          int NOT NULL REFERENCES erp.talhoes(id),
    gatilho            jsonb NOT NULL,        -- ex.: {"chuva_mm": 80, "janela_h": 24}
    previsao_snapshot  jsonb NOT NULL,        -- a previsão usada, guardada como prova
    status             text NOT NULL DEFAULT 'aberto',
    criado_em          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE agente.planos (
    id               serial PRIMARY KEY,
    incidente_id     int NOT NULL REFERENCES agente.incidentes(id),
    opcoes_json      jsonb NOT NULL,
    opcao_escolhida  int,
    aprovado_por     text,                    -- preenchido só quando o gerente diz SIM
    respondido_em    timestamptz
);

CREATE TABLE agente.auditoria (
    id             serial PRIMARY KEY,
    incidente_id   int REFERENCES agente.incidentes(id),
    ts             timestamptz NOT NULL DEFAULT now(),
    no             text NOT NULL,             -- quem fez: 'acao_preventiva', 'executor'...
    acao           text NOT NULL,             -- 'suspender_os', 'reagendar_os'...
    antes          jsonb,
    depois         jsonb,
    justificativa  text
);
```

`jsonb` é uma coluna que guarda um JSON inteiro. Use-a quando o formato varia ou quando você só quer guardar e mostrar, sem filtrar por campo.

### 6.4 `900_seed.sql`: a fazenda de exemplo

Regras para o seed:
1. **Datas relativas a `now()`**, nunca fixas. Uma OS marcada para "2026-10-30 07:00" vira passado no dia da demo e nada dispara. Use `now() + interval '20 hours'`.
2. Coordenadas reais de Goiás, porque o monitor do Edison consulta o Open-Meteo com elas. Rio Verde fica em torno de `-17.79, -50.92`.
3. Os nomes de produto saem do contrato 1, combinados com o Edison.
4. O cenário principal precisa de **uma OS de pulverização de defensivo nas próximas 24 h**, com máquina e operador. Crie também 2 ou 3 OS fora da janela de risco, para mostrar que o sistema só mexe na OS certa, e uma segunda máquina livre, para o plano ter para onde remarcar.

```sql
INSERT INTO erp.talhoes (nome, area_ha, cultura, data_plantio, estagio_fenologico, lat, lon, tipo_solo) VALUES
  ('Talhão 1 - Sede',   120.0, 'milho', current_date - 30, 'V6', -17.790, -50.920, 'latossolo'),
  ('Talhão 2 - Baixada', 85.5, 'soja',  current_date - 45, 'V8', -17.810, -50.900, 'latossolo');

INSERT INTO erp.insumos (produto, tipo, unidade, preco_unitario, estoque, bula_id) VALUES
  ('Fungicida X', 'fungicida', 'L', 120.00, 500, NULL);   -- nome, preço e bula_id: trocar pelo defensivo escolhido com o Edison (contrato 1)

INSERT INTO erp.maquinas (nome, tipo) VALUES
  ('Pulverizador A', 'pulverizador'),
  ('Pulverizador B', 'pulverizador');

INSERT INTO erp.operadores (nome, telegram_chat_id) VALUES
  ('João', NULL);   -- preencha com o seu chat_id para receber as mensagens de teste

INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, insumo_id, dose_por_ha, inicio_previsto, maquina_id, operador_id) VALUES
  (1, 'pulverizacao', 1, 0.5, now() + interval '20 hours', 1, 1),   -- a OS do cenário (0,5 L/ha)
  (2, 'pulverizacao', 1, 0.5, now() + interval '6 days',  1, 1);    -- fora da janela de risco

INSERT INTO erp.leituras_solo (talhao_id, ts, umidade_pct)
SELECT 1, now() - (h || ' hours')::interval, 28 + random() * 4
FROM generate_series(0, 47) AS h;
```

**Como saber que deu certo:** `docker compose up -d postgres`, conecte pelo DBeaver em `localhost:5432` e rode `SELECT * FROM erp.ordens_servico;`. Devem aparecer as duas OS com status `agendada`.

---

## 7. Servidor MCP do ERP

### 7.1 O que é, comparando com REST

| Numa API REST | No MCP |
|---|---|
| Rota `GET /os?inicio=...` | Ferramenta `listar_os(inicio, fim)` |
| Documentação no Swagger | A **docstring** e os tipos da função; **a IA lê isso para decidir quando e como chamar** |
| Cliente é um front-end | Cliente é o agente de IA do Gustavo |

Ou seja, você escreve funções Python normais com um decorator. Escreva docstrings claras em português, porque elas são a documentação que a IA consome.

### 7.2 Esqueleto

`erp_mcp/erp_mcp/db.py`:

```python
import os
import psycopg
from psycopg.rows import dict_row

def conectar():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)
```

`erp_mcp/erp_mcp/server.py`:

```python
from mcp.server.fastmcp import FastMCP
from erp_mcp import leitura, escrita

mcp = FastMCP("erp", host="0.0.0.0", port=8030)

for funcao in [leitura.listar_os, leitura.detalhar_os, leitura.disponibilidade_maquinas,
               leitura.listar_operadores, leitura.consultar_talhao, leitura.leituras_solo,
               escrita.suspender_os, escrita.reagendar_os, escrita.alocar_maquina,
               escrita.reservar_estoque]:
    mcp.tool()(funcao)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")   # fica em http://localhost:8030/mcp
```

Exemplo de ferramenta de leitura (`leitura.py`):

```python
from datetime import datetime
from erp_mcp.db import conectar

def listar_os(inicio: datetime, fim: datetime, talhao_id: int | None = None) -> list[dict]:
    """Lista as ordens de serviço com início previsto entre `inicio` e `fim`.

    Retorna id, talhão, tipo de operação, insumo, dose por hectare, início previsto,
    máquina, operador e status. Use `talhao_id` para filtrar um talhão.
    """
    sql = """
        SELECT os.*, t.nome AS talhao, t.area_ha, i.produto, i.unidade, i.preco_unitario
        FROM erp.ordens_servico os
        JOIN erp.talhoes t ON t.id = os.talhao_id
        LEFT JOIN erp.insumos i ON i.id = os.insumo_id
        WHERE os.inicio_previsto BETWEEN %s AND %s
          AND (%s::int IS NULL OR os.talhao_id = %s)
        ORDER BY os.inicio_previsto
    """
    with conectar() as conn:
        return conn.execute(sql, (inicio, fim, talhao_id, talhao_id)).fetchall()
```

### 7.3 Lista de ferramentas (contrato 5)

**Leitura**, sem risco:

| Ferramenta | Parâmetros | Retorna |
|---|---|---|
| `listar_os` | `inicio`, `fim`, `talhao_id?` | OS na janela, com dados do talhão e do insumo |
| `detalhar_os` | `os_id` | Uma OS completa |
| `disponibilidade_maquinas` | `inicio`, `fim`, `tipo?` | Máquinas com `status='disponivel'` **sem** OS `agendada`/`reagendada` sobrepondo o intervalo |
| `listar_operadores` | (nenhum) | Operadores e chat_id |
| `consultar_talhao` | `talhao_id` | Talhão completo (cultura, estágio, área, lat/lon) |
| `leituras_solo` | `talhao_id`, `horas=24` | Últimas leituras de umidade |

Dica para `disponibilidade_maquinas`: dois intervalos `[a1, a2]` e `[b1, b2]` se sobrepõem quando `a1 < b2 AND b1 < a2`. O fim de uma OS é `inicio_previsto + duracao_horas * interval '1 hour'`.

**Escrita**, a parte que exige cuidado:

| Ferramenta | Faz | Regra |
|---|---|---|
| `suspender_os(os_id, incidente_id, motivo)` | `agendada → suspensa_preventiva` | **Única sem aprovação.** Só funciona se a OS estiver `agendada` |
| `reagendar_os(os_id, novo_inicio, plano_id)` | `suspensa_preventiva → reagendada` | Exige plano aprovado |
| `alocar_maquina(os_id, maquina_id, plano_id)` | Troca a máquina | Exige plano aprovado e máquina livre |
| `reservar_estoque(insumo_id, quantidade, plano_id)` | Baixa o estoque | Exige plano aprovado e estoque suficiente |

### 7.4 As três regras de ouro da escrita

**Regra 1: a transição de status é checada no próprio `UPDATE`.**

```python
def suspender_os(os_id: int, incidente_id: int, motivo: str) -> dict:
    """Suspende preventivamente uma OS agendada (agendada → suspensa_preventiva).

    É a única alteração permitida sem aprovação do gerente, porque é reversível.
    Falha se a OS não estiver com status 'agendada'.
    """
    with conectar() as conn:                       # tudo numa transação
        antes = conn.execute("SELECT * FROM erp.ordens_servico WHERE id = %s", (os_id,)).fetchone()
        depois = conn.execute(
            """UPDATE erp.ordens_servico SET status = 'suspensa_preventiva'
               WHERE id = %s AND status = 'agendada'
               RETURNING *""",
            (os_id,),
        ).fetchone()
        if depois is None:
            return {"ok": False, "erro": f"OS {os_id} não está agendada (status atual: {antes and antes['status']})"}
        registrar_auditoria(conn, incidente_id, "acao_preventiva", "suspender_os", antes, depois, motivo)
        return {"ok": True, "os": depois}
```

O `AND status = 'agendada'` dentro do `UPDATE` é o que garante a regra. Se você fizesse um `SELECT`, um `if` no Python e só depois o `UPDATE`, duas chamadas quase simultâneas poderiam passar as duas pelo `if`. Assim, o banco resolve sozinho.

**Regra 2: toda escrita grava uma linha em `agente.auditoria`, na mesma transação.** Com `antes` e `depois` em JSON, o painel consegue mostrar "o que mudou e por quê". Na demo, isso é ouro. Para gravar um `dict` numa coluna `jsonb`, use `psycopg.types.json.Jsonb(antes)`. Lembre que `datetime` e `Decimal` não viram JSON sozinhos; converta com `json.dumps(..., default=str)` antes, ou escreva uma função auxiliar.

**Regra 3: as ferramentas que comprometem recursos verificam o plano no banco.**

```python
def _plano_aprovado(conn, plano_id: int) -> bool:
    linha = conn.execute(
        "SELECT 1 FROM agente.planos WHERE id = %s AND aprovado_por IS NOT NULL AND respondido_em IS NOT NULL",
        (plano_id,),
    ).fetchone()
    return linha is not None
```

Se o plano não estiver aprovado, a ferramenta devolve `{"ok": False, "erro": "plano não aprovado"}` e não altera nada. Mesmo que a IA "decida" reagendar por conta própria, o banco não deixa. Esse é um dos argumentos do pitch.

> Por que devolver `{"ok": False, ...}` em vez de lançar exceção? Porque quem lê o retorno é uma IA: uma mensagem clara ajuda o agente a entender o que aconteceu. Combine o formato com o Gustavo.

### 7.5 Como testar sem a IA

1. `uv run python -m erp_mcp.server`
2. Em outro terminal: `npx @modelcontextprotocol/inspector`
3. No navegador do Inspector, escolha "Streamable HTTP", URL `http://localhost:8030/mcp`, conecte, abra "Tools" e chame `listar_os`.
4. Chame `suspender_os` duas vezes na mesma OS: a primeira devolve `ok: true` e a segunda `ok: false`. Confira a linha em `agente.auditoria`.

Escreva também testes com `pytest` para as ferramentas de escrita, chamando as funções direto (elas são Python comum). Os casos mínimos:
- suspender uma OS agendada funciona;
- suspender de novo falha;
- reagendar sem plano aprovado falha;
- reservar mais que o estoque falha.

---

## 8. Canal de notificação

### 8.1 A "interface" `NotificationChannel`

A ideia: o código do Gustavo não sabe se a mensagem vai pelo Telegram ou pelo painel. Ele chama sempre os mesmos métodos, e a variável `CHANNEL` decide qual implementação é usada. Se o Telegram falhar no palco, trocamos `CHANNEL=painel` e nada mais muda.

`channels/channels/base.py`:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

@dataclass
class Resposta:
    incidente_id: int
    plano_id: int
    resposta: Literal["SIM", "NAO", "OPCOES"]
    respondido_por: str

AoResponder = Callable[[Resposta], Awaitable[None]]   # função que o Gustavo fornece

class NotificationChannel(ABC):
    @abstractmethod
    async def enviar_alerta(self, texto: str) -> None:
        """Mensagem simples para o gerente (sem botões)."""

    @abstractmethod
    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None:
        """Mensagem com os botões SIM / NÃO / Ver opções."""

    @abstractmethod
    async def notificar_operador(self, chat_id: int, texto: str) -> None:
        """Aviso para o operador da máquina."""

def criar_canal(nome: str, ao_responder: AoResponder) -> NotificationChannel:
    if nome == "telegram":
        from channels.telegram import TelegramChannel
        return TelegramChannel(ao_responder)
    from channels.painel import PainelChannel
    return PainelChannel()
```

`async`/`await`: a biblioteca do Telegram e o ADK trabalham de forma **assíncrona** (o programa não fica parado esperando a rede). Na prática, as funções são declaradas com `async def` e chamadas com `await`.

### 8.2 Telegram

**Preparação (dia 1, 15 min):**
1. No Telegram, fale com **@BotFather**, mande `/newbot`, escolha nome e usuário. Ele devolve o **token**, que é a senha do bot. Coloque-o no `.env` e **nunca** no git.
2. Mande qualquer mensagem para o seu bot.
3. Abra `https://api.telegram.org/bot<TOKEN>/getUpdates` no navegador e procure `"chat":{"id": 123456789`. Esse número é o seu **chat_id**; vá para `GERENTE_CHAT_ID`.

**Envio com botões** (`telegram.py`):

```python
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes
from channels.base import NotificationChannel, Resposta

class TelegramChannel(NotificationChannel):
    def __init__(self, ao_responder):
        self.ao_responder = ao_responder
        self.gerente = int(os.environ["GERENTE_CHAT_ID"])
        self.app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
        self.app.add_handler(CallbackQueryHandler(self._ao_clicar))

    async def pedir_aprovacao(self, incidente_id, plano_id, texto):
        botoes = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ SIM", callback_data=f"r:{incidente_id}:{plano_id}:SIM"),
            InlineKeyboardButton("❌ NÃO", callback_data=f"r:{incidente_id}:{plano_id}:NAO"),
            InlineKeyboardButton("🔎 Ver opções", callback_data=f"r:{incidente_id}:{plano_id}:OPCOES"),
        ]])
        await self.app.bot.send_message(self.gerente, texto, reply_markup=botoes)

    async def _ao_clicar(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()                                    # tira o "relógio" do botão
        _, incidente_id, plano_id, resposta = query.data.split(":")
        await query.edit_message_reply_markup(reply_markup=None)  # remove os botões: evita clique duplo
        await query.message.reply_text(f"Resposta registrada: {resposta}")
        await self.ao_responder(Resposta(int(incidente_id), int(plano_id), resposta,
                                         query.from_user.full_name))

    async def iniciar(self):
        """Liga o long polling sem travar o resto do programa."""
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

    # enviar_alerta e notificar_operador: self.app.bot.send_message(chat_id, texto)
```

Pontos de atenção:
- `callback_data` aceita **no máximo 64 bytes**. Mande só ids, nunca o texto do plano.
- Sem `query.answer()`, o botão fica "carregando" para sempre no celular do gerente.
- **Long polling**: o bot pergunta ao Telegram de tempos em tempos "tem clique novo?". Não precisa de URL pública nem HTTPS. Só pode existir **um** processo fazendo polling com o mesmo token; se você deixar um rodando no seu PC e outro no Docker, aparece o erro `Conflict: terminated by other getUpdates request`.
- Por que `iniciar()` em vez de `app.run_polling()`: `run_polling()` toma conta do programa inteiro, e o bot vai rodar dentro do processo do Gustavo junto com o ADK. Combine com ele onde `iniciar()` é chamado.

**Teste isolado (antes de integrar):** um `teste_bot.py` com um `ao_responder` falso que só dá `print(resposta)`. Mande uma aprovação para você mesmo, clique em SIM e veja o print.

### 8.3 Painel como canal (fallback)

`PainelChannel` não envia nada. Ele grava a pendência no banco (o plano em `agente.planos` com `respondido_em IS NULL`; o alerta pode ir para `agente.auditoria`) e a tela do painel mostra os botões. Quando o gerente clica no painel, o painel chama (contrato 6):

```
POST {AGENTS_URL}/incidentes/{incidente_id}/resposta
{"plano_id": 7, "resposta": "SIM", "respondido_por": "painel"}
```

---

## 9. Painel web

FastAPI com páginas HTML (Jinja2). Para atualizar ao vivo sem escrever JavaScript, use **HTMX**: um atributo no HTML que pede um pedaço da página a cada N segundos.

`painel/painel/templates/os.html` (trecho):

```html
<script src="/static/htmx.min.js"></script>
<table>
  <tbody hx-get="/parcial/os" hx-trigger="every 2s" hx-swap="innerHTML">
    {% include "_linhas_os.html" %}
  </tbody>
</table>
```

Baixe o `htmx.min.js` uma vez para `painel/painel/static/` e sirva-o pelo FastAPI (`app.mount("/static", StaticFiles(...))`), em vez de carregá-lo de um CDN. Se a internet do evento cair, o painel continua funcionando.

A rota `/parcial/os` devolve só as `<tr>` da tabela, renderizando `_linhas_os.html`. É um CRUD de listagem que se atualiza sozinho.

Telas, em ordem de prioridade:

| # | Tela | Conteúdo | Dia |
|---|---|---|---|
| 1 | **Ordens de serviço** | Tabela com talhão, operação, início, máquina, **status colorido** (verde agendada, amarelo suspensa, azul reagendada) | 3 |
| 2 | **Linha do tempo do incidente** | `agente.auditoria` em ordem: quem fez o quê, antes → depois, justificativa | 4 |
| 3 | **Aprovação pendente** | Planos com `respondido_em IS NULL` e botões SIM / NÃO / Ver opções | 4 |
| 4 | **Botão "Simular chuva"** | Chama `POST {MONITOR_URL}/replay` (contrato 8) | 4 |

> **Pense na demo.** Esta tela fica projetada enquanto o Gustavo fala. A banca precisa ver a OS **ficar amarela sozinha** segundos depois do "Simular chuva". Fonte grande, poucas colunas, contraste alto.

Não faça tela de login: é uma demo local e o tempo vale mais nas telas acima.

---

## 10. docker-compose

Um **container** por serviço, todos descritos em `docker-compose.yml` e lendo as variáveis de um único `.env`.

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: terramind
      POSTGRES_PASSWORD: terramind
      POSTGRES_DB: terramind
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/migrations:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U terramind"]
      interval: 3s
      retries: 20

  qdrant:
    image: qdrant/qdrant
    ports: ["6333:6333"]

  erp-mcp:
    build: ./erp_mcp
    env_file: .env
    ports: ["8030:8030"]
    depends_on:
      postgres: { condition: service_healthy }

  painel:
    build: ./painel
    env_file: .env
    ports: ["8080:8080"]
    depends_on:
      postgres: { condition: service_healthy }

  # agents (Gustavo), conhecimento e monitor (Edison): cada um adiciona o seu bloco

volumes:
  pgdata:
```

`.env.example` (vai para o git; o `.env` de verdade **não** vai, então coloque-o no `.gitignore`):

```
DATABASE_URL=postgresql://terramind:terramind@postgres:5432/terramind
TELEGRAM_BOT_TOKEN=
GERENTE_CHAT_ID=
CHANNEL=telegram            # telegram | painel
MONITOR_MODE=replay         # live | replay
APPROVAL_TIMEOUT_MIN=30
MODEL_NAME=
AGENTS_URL=http://agents:8000
MONITOR_URL=http://monitor:8020
ERP_MCP_URL=http://erp-mcp:8030/mcp
```

> ⚠️ **Dentro do Docker, `localhost` é o próprio container.** Entre containers, use o nome do serviço (`postgres`, `erp-mcp`). Do seu PC, fora do Docker, use `localhost:5432`. Esse é o erro número 1 de quem começa com compose.

`Dockerfile` padrão para os seus serviços Python:

```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .
CMD ["uv", "run", "python", "-m", "erp_mcp.server"]   # no painel: uvicorn painel.main:app --host 0.0.0.0 --port 8080
```

Comandos do dia a dia:

| Comando | Faz |
|---|---|
| `docker compose up -d --build` | Sobe tudo, reconstruindo o que mudou |
| `docker compose logs -f erp-mcp` | Mostra o log de um serviço ao vivo |
| `docker compose ps` | Quem está de pé |
| `docker compose down -v` | Derruba tudo **e apaga o banco** (para rodar migrations e seed de novo) |

---

## 11. Plano dia a dia

Marque os checkboxes conforme avança. O marco do time é: **no fim do dia 3, o fluxo inteiro roda com dados fake.**

### Dia 1: fundação
- [ ] Reunião de contratos (seção 4) e registro em `docs/contratos.md`
- [ ] Repositório com a estrutura de pastas, `.gitignore` (com `.env`) e `.env.example`
- [ ] `docker-compose.yml` só com `postgres`
- [ ] `001_erp.sql`, `002_agente.sql`, `900_seed.sql` → `SELECT` no DBeaver mostra os dados
- [ ] Servidor MCP com as ferramentas de **leitura** → testadas no MCP Inspector
- [ ] Bot criado no BotFather; `teste_bot.py` manda uma mensagem com botões para você

**Pronto quando:** o Gustavo consegue chamar `listar_os` a partir do ADK dele.

### Dia 2: escrita e resposta
- [ ] Ferramentas de **escrita** com as três regras de ouro + `pytest`
- [ ] `NotificationChannel`, `TelegramChannel` com `iniciar()` e clique → `ao_responder`
- [ ] Junto com o Gustavo: clicar SIM no Telegram faz a sessão dele continuar (é o maior risco técnico do projeto; testar hoje, não no dia 6)
- [ ] `erp-mcp` no compose

**Pronto quando:** `suspender_os` funciona via MCP e um clique no Telegram chega ao código do Gustavo.

### Dia 3: painel e ponta a ponta
- [ ] Painel: tela de OS com atualização a cada 2 s
- [ ] `painel` no compose
- [ ] Ensaiar com o time: incidente → OS fica amarela → mensagem no Telegram → SIM → OS fica azul

**Pronto quando:** o marco do time roda na sua máquina com `docker compose up`.

### Dia 4: fallback e demo
- [ ] Linha do tempo do incidente (auditoria)
- [ ] Aprovação pelo painel + `PainelChannel`
- [ ] Botão "Simular chuva"
- [ ] Testar `CHANNEL=painel`: o fluxo inteiro sem Telegram

### Dia 5: negócio
- [ ] **BMC** (Business Model Canvas) junto com o time
- [ ] Roteiro do pitch: a parte "o que a banca vê na tela"
- [ ] Ajustes no painel pedidos pelo time

### Dia 6: README e ensaio
- [ ] Seção do README: mini-ERP, MCP, canal e como subir (`docker compose up`)
- [ ] Ajudar o Edison no deploy na VM (o compose é seu, você conhece melhor)
- [ ] Testar casos de borda: NÃO, sem resposta, clique duplo, Telegram fora (`CHANNEL=painel`)

### Dia 7: congelamento
- [ ] Nada de código novo. Ensaio cronometrado; ajudar na gravação do vídeo de backup

---

## 12. Como usar IA para programar a sua parte

- **Dê contexto antes de pedir.** Cole a seção relevante deste documento e diga "implemente a ferramenta `disponibilidade_maquinas` conforme a tabela da seção 7.3". Pedidos vagos geram código que não bate com o contrato.
- **Peça uma coisa por vez** e rode antes de pedir a próxima.
- **Peça o teste junto:** "escreva também um teste pytest que prove que reagendar sem plano aprovado falha".
- **Não aceite nomes diferentes.** Se a IA sugerir `status = 'suspensa'` ou renomear uma ferramenta, corrija: os nomes são contrato com o time.
- **Quando der erro**, cole a mensagem inteira e o comando que você rodou. Peça para a IA explicar a causa antes de corrigir; assim você aprende e não só remenda.
- **Confira no banco.** Depois de qualquer escrita, um `SELECT` no DBeaver diz a verdade.

## 13. Quando pedir ajuda ao time

Peça ajuda se ficar **mais de 1 hora travado** no mesmo problema. Peça sempre que um contrato precisar mudar (nome de ferramenta, coluna, endereço), porque isso quebra o código dos outros. E peça antes de mexer em `agente.*` além do que está aqui, já que essa parte é compartilhada com o Gustavo.

---

## Glossário

| Termo | Explicação simples |
|---|---|
| **Agente (de IA)** | Programa em que uma IA (LLM) decide quais funções chamar para cumprir uma tarefa. No TerraMind são três: Agronômico, Operacional e Supervisor |
| **ADK** | Google Agent Development Kit, a biblioteca que o Gustavo usa para montar os agentes e a ordem em que eles rodam |
| **LLM** | Modelo de linguagem (Gemini, Claude, GPT...). Gera texto; aqui, nunca decide números sozinho |
| **MCP** | Model Context Protocol, um padrão para expor funções a agentes de IA. É "uma API feita para a IA chamar" |
| **Servidor MCP** | O seu programa que publica essas funções (as **ferramentas**) num endereço, como `http://erp-mcp:8030/mcp` |
| **Ferramenta (tool)** | Uma função que a IA pode chamar. A docstring e os tipos são a "documentação" que a IA lê |
| **Streamable HTTP** | O jeito como o servidor MCP conversa pela rede. Você só passa `transport="streamable-http"` |
| **ERP** | Sistema de gestão da empresa (estoque, ordens, máquinas). O nosso é um "mini-ERP" de mentira |
| **Schema (Postgres)** | Uma "pasta" de tabelas dentro do banco: `erp.talhoes`, `agente.incidentes`. Organiza quem é dono do quê |
| **Migration** | Arquivo SQL que cria ou altera tabelas, numerado para rodar em ordem |
| **Seed** | Dados iniciais de exemplo inseridos no banco |
| **Transação** | Conjunto de comandos SQL que acontecem todos ou nenhum. `with conectar() as conn:` faz commit no fim, ou rollback se der erro |
| **Máquina de estados / transição** | Regra de quais mudanças de status são permitidas (`agendada → suspensa_preventiva` sim; `executada → agendada` não) |
| **Auditoria** | Registro de quem mudou o quê, quando e por quê. Prova que o sistema é confiável |
| **Idempotência** | Fazer a mesma ação duas vezes não causa estrago. Ex.: clicar SIM duas vezes não reagenda duas vezes |
| **Incidente** | Um "caso" aberto pelo monitor quando a previsão bate o limite (ex.: 80 mm de chuva em 24 h) |
| **Plano** | A proposta dos agentes (nova data, máquina, custo) que o gerente aprova ou recusa |
| **Interface / classe abstrata** | Um "molde" que diz quais métodos uma classe tem que ter (`NotificationChannel`). Telegram e painel seguem o mesmo molde, então são trocáveis |
| **async / await** | Jeito de escrever código que espera a rede sem travar o programa inteiro |
| **BotFather** | O bot oficial do Telegram que cria outros bots e dá o token |
| **Token** | A senha do bot. Vazou, qualquer um controla o bot; fica só no `.env` |
| **chat_id** | O número que identifica uma conversa no Telegram (para quem o bot manda) |
| **Botões inline** | Botões que aparecem embaixo da mensagem no Telegram |
| **callback / callback_data** | Quando alguém clica num botão, o Telegram avisa o bot (callback) e manda junto um texto curto que você definiu (`callback_data`, até 64 bytes) |
| **Long polling** | O bot fica perguntando ao Telegram "tem novidade?". Alternativa ao **webhook**, em que o Telegram chama uma URL sua (que precisaria ser pública) |
| **Fallback** | Plano B. O painel é o fallback do Telegram |
| **Replay** | Rodar uma previsão do tempo gravada em arquivo em vez da real, para a chuva "acontecer" na hora da demo |
| **Docker / imagem / container** | Imagem = receita empacotada de um programa; container = essa receita rodando, isolada |
| **docker-compose** | Arquivo que descreve vários containers e sobe todos com um comando |
| **Volume** | Onde o Docker guarda dados que sobrevivem ao container ser recriado (o banco). `down -v` apaga |
| **Healthcheck** | Teste que o Docker roda para saber se o serviço está pronto (ex.: Postgres aceitando conexão) |
| **.env / variável de ambiente** | Configurações (senhas, URLs) fora do código, para trocar sem mexer no código |
| **HTMX** | Biblioteca que deixa um HTML se atualizar sozinho com atributos como `hx-get` e `hx-trigger` |
| **Jinja2** | Linguagem de template: HTML com `{{ variavel }}` e `{% for %}` |
| **uv** | Gerenciador de pacotes Python rápido (`uv add`, `uv run`) |
| **VM** | Máquina virtual na nuvem (GCP) onde a demo também vai rodar |
| **Talhão** | Pedaço da fazenda com uma cultura, a unidade de manejo |
| **Insumo** | O que é aplicado: adubo, herbicida, fungicida |
| **Bula** | Documento oficial do produto com as condições de uso (vento máximo, chuva...) |
| **Estágio fenológico** | Fase de desenvolvimento da planta (V6 = 6 folhas, R1 = início da floração...) |
| **Ordem de serviço (OS)** | Tarefa agendada: aplicar X no talhão Y, com a máquina Z, na hora H |
| **BMC** | Business Model Canvas, um quadro de uma página que descreve o modelo de negócio |
