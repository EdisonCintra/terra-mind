-- 002_agente.sql — Incidentes, planos e auditoria (contratos 2, 3, 8 e 9).
-- As tabelas de sessão do ADK (`sessions`, `events`, etc.) são criadas pelo próprio
-- `DatabaseSessionService` em `search_path=agente` (contrato 9).
CREATE SCHEMA IF NOT EXISTS agente;

CREATE TABLE agente.incidentes (
    id                 serial PRIMARY KEY,
    talhao_id          int NOT NULL REFERENCES erp.talhoes(id),
    gatilho            jsonb NOT NULL
        CHECK (jsonb_typeof(gatilho) = 'array'),            -- contrato 3: lista de gatilhos (tipo, valor, limiar, inicio, fim)
    previsao_snapshot  jsonb NOT NULL
        CHECK (jsonb_typeof(previsao_snapshot) = 'array'),  -- contrato 3: 120 h da previsão
    status             text NOT NULL DEFAULT 'aberto'
        CHECK (status IN ('aberto', 'aguardando_aprovacao', 'aguardando_decisao_manual', 'encerrado')),
    criado_em          timestamptz NOT NULL DEFAULT now()
);

-- Contrato 3: só pode existir um incidente não encerrado por talhão.
CREATE UNIQUE INDEX idx_incidentes_talhao_ativo
    ON agente.incidentes (talhao_id)
    WHERE status <> 'encerrado';

CREATE TABLE agente.planos (
    id               serial PRIMARY KEY,
    incidente_id     int NOT NULL REFERENCES agente.incidentes(id),
    opcoes_json      jsonb NOT NULL,
    opcao_escolhida  int,
    aprovado_por     text,                                  -- preenchido apenas quando a resposta é SIM
    respondido_em    timestamptz,
    criado_em        timestamptz NOT NULL DEFAULT now(),    -- contrato 8: usado pelo timeout
    escalado_em      timestamptz                            -- contrato 8: preenchido no escalonamento único
);

CREATE TABLE agente.auditoria (
    id             serial PRIMARY KEY,
    incidente_id   int REFERENCES agente.incidentes(id),
    ts             timestamptz NOT NULL DEFAULT now(),
    no             text NOT NULL,                           -- ex.: 'acao_preventiva', 'executor', 'painel'
    acao           text NOT NULL,                           -- ex.: 'suspender_os', 'reagendar_os', 'enviar_alerta'
    antes          jsonb,
    depois         jsonb,
    justificativa  text
);

CREATE INDEX idx_auditoria_incidente_ts
    ON agente.auditoria (incidente_id, ts);

