-- Só o que o monitor lê e escreve (contrato 3), até existirem o 001_erp.sql e o 002_agente.sql do Felipe.
CREATE SCHEMA IF NOT EXISTS erp;
CREATE TABLE erp.talhoes (id serial PRIMARY KEY, nome text NOT NULL, lat numeric(9,6) NOT NULL, lon numeric(9,6) NOT NULL);
CREATE SCHEMA IF NOT EXISTS agente;
CREATE TABLE agente.incidentes (
    id serial PRIMARY KEY,
    talhao_id int NOT NULL REFERENCES erp.talhoes(id),
    gatilho jsonb NOT NULL,
    previsao_snapshot jsonb NOT NULL,
    status text NOT NULL DEFAULT 'aberto',
    criado_em timestamptz NOT NULL DEFAULT now()
);
