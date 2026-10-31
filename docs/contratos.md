# TerraMind · Contratos e decisões do time

Fonte única dos combinados entre as frentes: nomes, formatos, endereços e decisões que mudam a spec. Com um contrato fechado, cada frente programa sozinha sem quebrar a outra.

**Como manter:**
- Quem muda um contrato atualiza esta página no mesmo PR da mudança e avisa no grupo.
- Não renumerar. Contrato novo entra no fim; contrato que deixou de valer fica riscado, com a data.
- Status: **Fechado** (combinado pelos envolvidos), **Proposto** (escrito por uma frente, aguarda ok), **Aberto** (sem proposta fechada).
- Os planos em [docs/plans/](plans/) citam os números daqui.

Última atualização: 30/10/2026 (Edison).

## Decisões

| ID | Decisão | Quem | Data | Status |
|---|---|---|---|---|
| A1–A3, A6, A7 | Registradas pelo Gustavo; a preencher por ele | Gustavo | 29/10 | A preencher |
| A4 | O Avaliador de Risco é um LLM e decide a violação a partir dos trechos do Qdrant (`buscar_trechos`), citando fonte e página | Gustavo | 29/10 | Fechado |
| A5 | Se o Avaliador falhar ou não achar evidência, a OS **não** é suspensa: o gerente é alertado e o incidente fica em `aguardando_decisao_manual` | Gustavo | 29/10 | Fechado |
| D1 | Decisão só por RAG: sem extração de dados estruturados, sem `regras_aplicacao`, `tolerancia_fenologica`, revisão manual nem `consultar_regras` | Gustavo | 29/10 | Fechado |
| D2 | **Foco só em defensivos agrícolas** (fungicida, herbicida, inseticida). Adubação sai do escopo: fertilizante não tem bula no Agrofit e a referência técnica não garante um limite de chuva citável. Cenário da demo: chuva intensa × pulverização de defensivo agendada | Edison | 29/10 | Proposto: aguarda ok do Gustavo e do Felipe |
| D3 | Monorepo com **um ambiente Python na raiz** (`pyproject.toml` + `uv.lock`); cada serviço é uma pasta com código direto, sem pasta aninhada. A pasta da base de conhecimento se chama `base_agricola/`; schema, container e variável continuam `conhecimento` | Edison | 29/10 | Proposto: aguarda ok do Gustavo e do Felipe |
| D4 | `janelas_validas` é código determinístico em `agents/rules/` (Gustavo), não ferramenta do MCP | Gustavo | 29/10 | Fechado (spec) |
| D5 | **Bulas da demo:** só defensivos cuja bula traz o limite de chuva escrito: Abacus HC (p. 7) e Ativum (p. 6), fungicidas BASF; Brucia (p. 9) e Amplexus (p. 5), herbicidas. **Produto da OS do cenário: Abacus HC** (registrado para soja e milho) | Edison | 30/10 | Proposto: aguarda ok do Felipe |

## Contratos

Números de 1 a 12 seguem o plano do Gustavo. Os itens 1, 2 e 4 foram inferidos dos planos e precisam de confirmação dele. De 13 em diante são contratos que já existiam nos planos sem número do time.

| # | Contrato | Combinado | Entre | Status |
|---|---|---|---|---|
| 1 | Nome do produto | `erp.insumos.produto` tem exatamente o mesmo texto que o `produto` do `fontes.yaml` e o `Trecho.produto` no Qdrant. `erp.insumos.bula_id` = `conhecimento.documentos.id`. O Avaliador filtra os trechos por esse nome. Valores: `Abacus HC` → `bula-abacus-hc`, `Ativum` → `bula-ativum` (tipo `fungicida`); `Brucia` → `bula-brucia`, `Amplexus` → `bula-amplexus` (tipo `herbicida`) | Felipe, Edison | Proposto: o seed do Felipe ainda tem `Fungicida X` |
| 2 | Migrations | SQL numerados em `db/migrations/`, rodados em ordem alfabética pelo Postgres na criação do banco: `001_erp.sql`, `002_agente.sql` (Felipe), `010_conhecimento.sql` (Edison), `900_seed.sql` (Felipe) | Felipe, Edison, Gustavo | Fechado (confirmar número) |
| 3 | Abertura de incidente | O Monitor faz INSERT em `agente.incidentes` com `status = 'aberto'`, `gatilho` = lista de gatilhos (`tipo`, `valor`, `limiar`, `inicio`, `fim`) e `previsao_snapshot` = as 120 h da previsão; depois chama `POST {AGENTS_URL}/incidentes/{id}/iniciar` (202, idempotente). Formato do `gatilho`: lista de `{tipo: chuva|vento|umidade, valor, limiar, inicio, fim}`, com datas ISO 8601 com fuso. Formato do `previsao_snapshot`: lista de 120 `{ts, chuva_mm, vento_kmh, umidade_pct, temp_c}`, uma por hora, a partir da hora atual. Só abre incidente num talhão sem nenhum incidente com status diferente de `encerrado`. Depois da criação, só `agents` muda o status (`aberto` → `aguardando_aprovacao` → `encerrado`, ou `aguardando_decisao_manual`) | Edison, Gustavo | Fechado |
| 4 | Endereço do MCP | `http://erp-mcp:8030/mcp`, transporte streamable HTTP | Felipe, Gustavo | Fechado (confirmar número) |
| 5 | Ferramentas MCP | Nomes da seção 7 do plano do Felipe; toda resposta no formato `{"ok": ...}`. `suspender_os` só faz `agendada → suspensa_preventiva`; as demais escritas exigem plano aprovado | Felipe, Gustavo | Fechado |
| 6, 7 | Canal e resposta do gerente | `Resposta`, `ao_responder`, `criar_canal`, `iniciar()` (plano do Gustavo). O Telegram chama `ao_responder` dentro do processo `agents`; o painel chama `POST {AGENTS_URL}/incidentes/{id}/resposta` com `{plano_id, resposta, respondido_por}`. Resposta repetida para o mesmo plano → 409 | Felipe, Gustavo | Fechado |
| 8, 9 | Tabelas do schema `agente` | `agente.planos` com `criado_em` e `escalado_em`; sessões do ADK com `search_path=agente` | Felipe, Gustavo | Fechado |
| 10 | `buscar_trechos` | `buscar_trechos(consulta: str, produto: str \| None = None, secao: str \| None = None) -> {"trechos": [{texto, fonte_doc, pagina, secao, produto}]}`, em `agents/tools/conhecimento.py`, sobre `POST {CONHECIMENTO_URL}/trechos/busca`. Lista vazia = o documento não traz o limite. Erro de rede, timeout de 5 s ou 5xx → `{"trechos": [], "erro": "servico_conhecimento_indisponivel"}`. **Para achar limites, filtrar só por `produto`, sem `secao`:** cada bula põe o limite de chuva numa seção diferente (`condicoes_climaticas`, `limitacoes_uso`, `modo_aplicacao`). Valores de `secao`: `condicoes_climaticas`, `modo_aplicacao`, `intervalo_seguranca`, `limitacoes_uso`, `outra`, `geral` | Edison, Gustavo | Fechado |
| 11 | Fatores de perda | `GET {CONHECIMENTO_URL}/fatores-perda?tipo_insumo=&condicao=` → `{tipo_insumo, condicao, fator, fonte_ou_estimativa}` ou 404. `condicao` ∈ {`chuva`, `vento`, `umidade`, `temperatura`}, os mesmos valores de `ViolacaoOs.parametro`. Sem fator, a perda fica em branco. Fatores carregados hoje: `fungicida/chuva` e `herbicida/chuva` = 0,5, **declarados como estimativa** (as bulas não quantificam a perda; revisar antes do pitch) | Edison, Gustavo | Fechado (valor 0,5 a revisar) |
| 12 | Unidades e fuso | Vento em km/h, temperatura em °C, umidade em %, chuva em mm, tempo em horas; todo `datetime` com fuso `America/Sao_Paulo` | todos | Fechado |
| 13 | Portas | agents 8000 · conhecimento 8010 · monitor 8020 · erp-mcp 8030 · painel 8080 · postgres 5432 · qdrant 6333 | todos | Fechado |
| 14 | Botão "Simular chuva" | O painel chama `POST {MONITOR_URL}/replay` com `{"cenario": "chuva_80mm.json", "talhao_id"?: int}` e recebe `{"incidentes": [...], "erros": [...]}`. Sem `talhao_id`, o cenário vale para todos os talhões (um incidente por talhão sem incidente aberto). O cenário tem 80 mm entre a 14ª e a 33ª hora a partir de agora, cobrindo a OS do seed (+20 h), e fica seco depois da 34ª | Felipe, Edison | Implementado; aguarda o painel |
| 15 | Tipo do insumo | `erp.insumos.tipo` ∈ {`fungicida`, `herbicida`, `inseticida`} (D2). `fatores_perda.tipo_insumo` usa exatamente esses valores, para o `calcular_impacto` buscar o fator com o tipo que vem do ERP | Felipe, Edison, Gustavo | Proposto |

| 16 | Variáveis dos serviços do Edison | `conhecimento`: `DATABASE_URL`, `QDRANT_URL`, `EMBEDDING_MODEL` (opcional). `monitor`: `DATABASE_URL`, `AGENTS_URL`, `MONITOR_MODE` (`replay` padrão, ou `live`), `MONITOR_INTERVAL_MIN` (30), `LIMIAR_CHUVA_MM` (20), `LIMIAR_VENTO_KMH` (15), `LIMIAR_UMIDADE_MIN_PCT` (50), `JANELA_RISCO_H` (48). `DATABASE_URL` aceita `postgresql://` ou `postgresql+psycopg://`. Ferramenta `buscar_trechos` (dentro do `agents`): `CONHECIMENTO_URL` | todos | Implementado |

## Pendências

| O quê | Com quem | Até |
|---|---|---|
| Preencher A1–A3, A6, A7 e confirmar os números 1, 2 e 4 | Gustavo | dia 2 |
| Ok para D2 (só defensivos) e D3 (ambiente na raiz) | Gustavo, Felipe | dia 1 |
| Fechar os valores de `tipo_insumo` (contrato 15) | Felipe, Edison, Gustavo | dia 3 |
| Trocar `Fungicida X` por `Abacus HC` no seed e colar os blocos `conhecimento` e `monitor` do README no compose (contratos 1 e D5) | Felipe | dia 2 |
| Avaliador buscar com `produto` e sem `secao`; expor `POST /incidentes/{id}/iniciar` (contratos 3 e 10) | Gustavo | dia 3 |
| Revisar o fator de perda 0,5 (estimativa) antes do pitch (contrato 11) | Time | dia 5 |
