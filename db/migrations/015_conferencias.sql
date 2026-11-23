-- 015_conferencias.sql — Conferência das suspensões ao vivo contra a chuva observada (E6). Idempotente.
CREATE SCHEMA IF NOT EXISTS clima;

CREATE TABLE IF NOT EXISTS clima.conferencias (
    incidente_id       int NOT NULL REFERENCES agente.incidentes(id),
    os_id              int NOT NULL REFERENCES erp.ordens_servico(id),
    janela_inicio      timestamptz,
    janela_fim         timestamptz,
    chuva_prevista_mm  numeric,
    chuva_observada_mm numeric,
    limite_mm          numeric,
    limite_fonte       text,
    fonte_observado    text CHECK (fonte_observado IN ('estacao', 'reanalise')),
    estacao            text,
    classe             text NOT NULL CHECK (classe IN ('correta', 'falso_alarme', 'sem_dado')),
    motivo             text,
    conferido_em       timestamptz DEFAULT now(),
    PRIMARY KEY (incidente_id, os_id)
);
