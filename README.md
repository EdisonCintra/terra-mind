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
curl -X POST localhost:8020/replay -H 'content-type: application/json' -d '{"cenario":"chuva_80mm.json"}'
```

No Windows, mande corpos com acento a partir de um arquivo UTF-8 (`--data-binary @arquivo.json`): o terminal pode enviar outra codificação e a API responde 400.

### Subida no Docker

O `docker compose up -d --build` já sobe o `conhecimento` e o `monitor`. O serviço `conhecimento-carga` roda antes, indexa as bulas no Qdrant e carrega os fatores de perda; se alguma bula falhar, o `conhecimento` não sobe.

As migrations e o seed só rodam com o volume do Postgres vazio, e as datas das OS contam a partir desse momento. Antes de cada ensaio ou apresentação, recrie o banco:

```bash
docker compose down -v
docker compose up -d --build
```
