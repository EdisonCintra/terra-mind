-- 005_safra.sql — Frente do Felipe (faixa 003–009): planejamento de safra (F5).
-- "O que está plantado" já está em erp.talhoes (cultura, data_plantio e estágio). "O que vai ser plantado"
-- não existia: uma linha por talhão e safra, com a cultura e a data de plantio previstas.
-- Idempotente, como as demais.

CREATE TABLE IF NOT EXISTS erp.planejamento_safra (
    id                serial PRIMARY KEY,
    talhao_id         int NOT NULL REFERENCES erp.talhoes(id) ON DELETE CASCADE,
    safra             text NOT NULL,                      -- ex.: '2027/28'
    cultura           text NOT NULL,                      -- mesma lista de erp.talhoes.cultura ('milho', 'soja')
    plantio_previsto  date NOT NULL,
    status            text NOT NULL DEFAULT 'planejado'
        CHECK (status IN ('planejado', 'plantado', 'cancelado')),
    observacoes       text,
    UNIQUE (talhao_id, safra)
);

CREATE INDEX IF NOT EXISTS idx_planejamento_safra_plantio
    ON erp.planejamento_safra (plantio_previsto) WHERE status = 'planejado';
