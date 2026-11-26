-- 006_cadastro.sql — Frente do Felipe (faixa 003–009): cadastro pelo painel (F1, contrato 18).
-- Idempotente, como as demais.

-- Contrato 18: geometria do talhão em GeoJSON (Point ou Polygon, WGS84, [lon, lat]). `lat` e `lon` continuam
-- e passam a ser o CENTRO da geometria (o Monitor usa essas colunas). Talhão antigo sem geometria vale como ponto.
ALTER TABLE erp.talhoes ADD COLUMN IF NOT EXISTS geom jsonb;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'talhoes_geom_valida') THEN
        ALTER TABLE erp.talhoes ADD CONSTRAINT talhoes_geom_valida
            CHECK (geom IS NULL OR (geom->>'type' IN ('Point', 'Polygon') AND jsonb_typeof(geom->'coordinates') = 'array'));
    END IF;
END $$;

UPDATE erp.talhoes
SET geom = jsonb_build_object('type', 'Point', 'coordinates', jsonb_build_array(lon::float8, lat::float8))
WHERE geom IS NULL;

-- Desativar em vez de apagar: OS, incidentes e relatórios antigos continuam apontando para o cadastro.
-- Desativado some das listas, não é vigiado pelo Monitor e não entra em OS nova.
ALTER TABLE erp.talhoes    ADD COLUMN IF NOT EXISTS ativo boolean NOT NULL DEFAULT true;
ALTER TABLE erp.maquinas   ADD COLUMN IF NOT EXISTS ativo boolean NOT NULL DEFAULT true;
ALTER TABLE erp.operadores ADD COLUMN IF NOT EXISTS ativo boolean NOT NULL DEFAULT true;
ALTER TABLE erp.insumos    ADD COLUMN IF NOT EXISTS ativo boolean NOT NULL DEFAULT true;
