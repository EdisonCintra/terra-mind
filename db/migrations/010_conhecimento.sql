-- Base de conhecimento (frente do Edison): documentos indexados e fatores de perda.
-- Os trechos ficam no Qdrant; aqui só o que precisa de SQL (contratos 1, 11 e 15).
CREATE SCHEMA IF NOT EXISTS conhecimento;

CREATE TABLE conhecimento.documentos (
    id        text PRIMARY KEY,                 -- = erp.insumos.bula_id (contrato 1)
    titulo    text NOT NULL,
    tipo_doc  text NOT NULL CHECK (tipo_doc IN ('bula', 'referencia', 'fenologia', 'zarc')),
    produto   text,                             -- = erp.insumos.produto (contrato 1)
    culturas  text[] NOT NULL DEFAULT '{}',
    url       text,
    arquivo   text
);

CREATE TABLE conhecimento.fatores_perda (
    id                   serial PRIMARY KEY,
    tipo_insumo          text NOT NULL,         -- valores de erp.insumos.tipo (contrato 15)
    condicao             text NOT NULL CHECK (condicao IN ('chuva', 'vento', 'umidade', 'temperatura')),
    fator                numeric NOT NULL CHECK (fator BETWEEN 0 AND 1),
    fonte_ou_estimativa  text NOT NULL,         -- "<doc_id>:p.<N>" ou "estimativa: <justificativa>"
    UNIQUE (tipo_insumo, condicao)
);
