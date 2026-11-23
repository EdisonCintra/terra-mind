-- 013_observacoes.sql — Estações e leituras observadas do clima (E3). Idempotente.
CREATE SCHEMA IF NOT EXISTS clima;

CREATE TABLE IF NOT EXISTS clima.estacoes (
    codigo text PRIMARY KEY,
    nome   text NOT NULL,
    lat    numeric(9,6) NOT NULL,
    lon    numeric(9,6) NOT NULL,
    fonte  text NOT NULL CHECK (fonte IN ('inmet', 'fazenda'))
);

CREATE TABLE IF NOT EXISTS clima.observacoes (
    estacao          text NOT NULL REFERENCES clima.estacoes(codigo),
    ts               timestamptz NOT NULL,
    chuva_mm         numeric,
    temp_c           numeric,
    umidade_pct      numeric,
    vento_kmh        numeric,
    vento_dir_graus  numeric,
    PRIMARY KEY (estacao, ts)
);
