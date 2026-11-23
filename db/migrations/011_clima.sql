-- 011_clima.sql — Previsões buscadas por talhão e origem do incidente (E2, contrato 27).
CREATE SCHEMA IF NOT EXISTS clima;

CREATE TABLE IF NOT EXISTS clima.previsoes (
    id         serial PRIMARY KEY,
    talhao_id  int NOT NULL REFERENCES erp.talhoes(id),
    buscada_em timestamptz NOT NULL DEFAULT now(),
    fonte      text NOT NULL CHECK (fonte IN ('open-meteo', 'replay')),
    horas      jsonb NOT NULL CHECK (jsonb_typeof(horas) = 'array'),
    dias       jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(dias) = 'array')
);

CREATE INDEX IF NOT EXISTS idx_previsoes_talhao_buscada
    ON clima.previsoes (talhao_id, buscada_em DESC);

-- 'live' = veio do clima real; 'replay' = veio da simulação (cenário gravado).
-- Incidentes que já existiam vêm da época em que o replay era o padrão: entram como 'replay',
-- para não inundar o Histórico (contrato 27). Só os novos nascem 'live'. Re-rodar não reescreve
-- linhas: o ADD é pulado e o SET DEFAULT só mexe no padrão.
ALTER TABLE agente.incidentes
    ADD COLUMN IF NOT EXISTS origem text NOT NULL DEFAULT 'replay'
    CHECK (origem IN ('live', 'replay'));
ALTER TABLE agente.incidentes ALTER COLUMN origem SET DEFAULT 'live';
