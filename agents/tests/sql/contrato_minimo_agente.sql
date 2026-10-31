-- Contrato mínimo para os testes enquanto db/migrations do Felipe não existem (contratos 4 e 8).
CREATE SCHEMA IF NOT EXISTS erp;
CREATE TABLE erp.talhoes (
    id    serial PRIMARY KEY,
    nome  text NOT NULL
);

CREATE SCHEMA IF NOT EXISTS agente;
CREATE TABLE agente.incidentes (
    id                 serial PRIMARY KEY,
    talhao_id          int NOT NULL REFERENCES erp.talhoes(id),
    gatilho            jsonb NOT NULL,
    previsao_snapshot  jsonb NOT NULL,
    status             text NOT NULL DEFAULT 'aberto',
    criado_em          timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE agente.planos (
    id               serial PRIMARY KEY,
    incidente_id     int NOT NULL REFERENCES agente.incidentes(id),
    opcoes_json      jsonb NOT NULL,
    opcao_escolhida  int,
    aprovado_por     text,
    respondido_em    timestamptz,
    criado_em        timestamptz NOT NULL DEFAULT now(),
    escalado_em      timestamptz
);
CREATE TABLE agente.auditoria (
    id             serial PRIMARY KEY,
    incidente_id   int REFERENCES agente.incidentes(id),
    ts             timestamptz NOT NULL DEFAULT now(),
    no             text NOT NULL,
    acao           text NOT NULL,
    antes          jsonb,
    depois         jsonb,
    justificativa  text
);
