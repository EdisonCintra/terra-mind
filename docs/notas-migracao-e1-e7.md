# Nota de migração: E1–E7 (migrations 011–015) e G1/G2/G3/G7 (020)

**Quem já tem a stack rodando precisa aplicar as migrations novas antes de subir esta versão.**

Por quê: ainda não há runner de migrations (vem com o F2). O Postgres só roda `db/migrations/` pelo
`docker-entrypoint-initdb.d`, e isso acontece apenas com o volume vazio. Num volume que já existe, as
tabelas e colunas novas não aparecem, e aí:

- o `conhecimento-carga` falha (lê `sha256` e `indexado_em` de `conhecimento.documentos`), e o
  `conhecimento` não sobe, porque depende dele. O "Pergunte à bula" fica fora do ar;
- todo ciclo do Monitor registra erro ao gravar em `clima.previsoes`;
- sem a `020_decisao.sql` (frente do Gustavo, já na `main`): a tela Perda evitada, os limites da bula da
  Agenda, a conclusão da OS e os relatórios de aplicação dão erro, e o KPI "Perda evitada" da Visão geral
  mostra "—". A 020 cria `agente.impactos`, `conhecimento.limites_produto`, `erp.execucoes_os` e
  `agente.relatorios_aplicacao`.

Escolha um dos caminhos:

1. **Recriar o banco** (perde os dados locais do Postgres):
   `docker compose down -v` e depois `docker compose up -d`.
2. **Aplicar à mão** (mantém os dados e o índice do Qdrant). As migrations 011–015 e a 020 são idempotentes,
   então rodar de novo não estraga nada. A ordem importa: a 011 antes da 020 (veja a seção seguinte).

   ```bash
   for f in db/migrations/01[1-5]_*.sql db/migrations/020_*.sql; do
     docker compose exec -T postgres psql -U terramind -d terramind -v ON_ERROR_STOP=1 -f - < "$f"
   done
   ```

## Incidentes antigos e a coluna `origem`

A 011 marca como `replay` os incidentes que já existiam, porque antes o replay era o padrão. Assim eles
ficam fora do Histórico e da Perda evitada. Só os incidentes novos nascem `live`.

Isso só acontece quando a 011 cria a coluna `origem`. Se a coluna já existia, a 011 não mexe em nenhuma
linha, e os incidentes antigos ficam como `live`. Há dois casos:

- a stack já aplicou a **011 antiga** (versão anterior desta branch, com `DEFAULT 'live'`);
- a stack aplicou a **020 da `main`** antes da 011 (a 020 também cria `origem` com `DEFAULT 'live'`).

Nesses casos, recrie o banco (`docker compose down -v`) ou marque os antigos à mão. Primeiro confira quais
incidentes `live` são anteriores à primeira previsão que o Monitor novo gravou. O Monitor desta branch grava
uma linha em `clima.previsoes` a cada ciclo, então antes dela só o Monitor antigo, em replay, abria incidentes:

```sql
SELECT id, talhao_id, status, criado_em
FROM agente.incidentes
WHERE origem = 'live'
  AND criado_em < (SELECT min(buscada_em) FROM clima.previsoes)
ORDER BY id;
```

Se a lista bate com as simulações ("Simular chuva", talhão 1), marque:

```sql
UPDATE agente.incidentes SET origem = 'replay'
WHERE origem = 'live'
  AND criado_em < (SELECT min(buscada_em) FROM clima.previsoes);
```

Exceção: se a stack rodou o Monitor da `main` com `MONITOR_MODE=live` e houve chuva real antes dessa data,
tire esses incidentes do `UPDATE` (ex.: `AND id NOT IN (...)`). Com `clima.previsoes` vazia, o `min` é nulo e o
`UPDATE` não muda nada. Nesse caso use uma data fixa, a do dia em que a stack passou a rodar esta versão.
