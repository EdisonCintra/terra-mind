-- 003_fazendas.sql — Frente do Felipe (faixa 003–009, contrato 29): várias fazendas (F2, contrato 20).
-- Organização > fazenda > talhões, máquinas, operadores, insumos e OS. Cada fazenda tem seus gerentes.
-- Aplicada pelo runner (`python -m db.migrar`) e idempotente: um banco que já tem dados recebe a
-- "Fazenda Exemplo" e tudo o que existia passa a pertencer a ela, sem apagar nada.

CREATE TABLE IF NOT EXISTS erp.organizacoes (
    id    serial PRIMARY KEY,
    nome  text NOT NULL
);

CREATE TABLE IF NOT EXISTS erp.fazendas (
    id              serial PRIMARY KEY,
    organizacao_id  int NOT NULL REFERENCES erp.organizacoes(id),
    nome            text NOT NULL,
    demo            boolean NOT NULL DEFAULT false,   -- E2: a "Fazenda Demo" só recebe o replay
    UNIQUE (organizacao_id, nome)
);

-- Quem recebe as mensagens da fazenda (aprovação e alertas). O F3 liga isto aos usuários do painel.
CREATE TABLE IF NOT EXISTS erp.gerentes (
    id                serial PRIMARY KEY,
    fazenda_id        int NOT NULL REFERENCES erp.fazendas(id),
    nome              text NOT NULL,
    telegram_chat_id  bigint,
    ativo             boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_gerentes_fazenda ON erp.gerentes (fazenda_id) WHERE ativo;

-- Banco que já tinha dados (criado antes desta migration): tudo vira da "Fazenda Exemplo".
INSERT INTO erp.organizacoes (nome)
SELECT 'Organização Exemplo'
WHERE NOT EXISTS (SELECT 1 FROM erp.fazendas)
  AND (EXISTS (SELECT 1 FROM erp.talhoes) OR EXISTS (SELECT 1 FROM erp.insumos)
       OR EXISTS (SELECT 1 FROM erp.maquinas) OR EXISTS (SELECT 1 FROM erp.operadores));

INSERT INTO erp.fazendas (organizacao_id, nome)
SELECT (SELECT min(id) FROM erp.organizacoes), 'Fazenda Exemplo'
WHERE NOT EXISTS (SELECT 1 FROM erp.fazendas)
  AND EXISTS (SELECT 1 FROM erp.organizacoes);

ALTER TABLE erp.talhoes        ADD COLUMN IF NOT EXISTS fazenda_id int REFERENCES erp.fazendas(id);
ALTER TABLE erp.maquinas       ADD COLUMN IF NOT EXISTS fazenda_id int REFERENCES erp.fazendas(id);
ALTER TABLE erp.operadores     ADD COLUMN IF NOT EXISTS fazenda_id int REFERENCES erp.fazendas(id);
ALTER TABLE erp.insumos        ADD COLUMN IF NOT EXISTS fazenda_id int REFERENCES erp.fazendas(id);
ALTER TABLE erp.ordens_servico ADD COLUMN IF NOT EXISTS fazenda_id int REFERENCES erp.fazendas(id);

UPDATE erp.talhoes    SET fazenda_id = (SELECT min(id) FROM erp.fazendas) WHERE fazenda_id IS NULL;
UPDATE erp.maquinas   SET fazenda_id = (SELECT min(id) FROM erp.fazendas) WHERE fazenda_id IS NULL;
UPDATE erp.operadores SET fazenda_id = (SELECT min(id) FROM erp.fazendas) WHERE fazenda_id IS NULL;
UPDATE erp.insumos    SET fazenda_id = (SELECT min(id) FROM erp.fazendas) WHERE fazenda_id IS NULL;
-- A OS é sempre da fazenda do talhão.
UPDATE erp.ordens_servico os SET fazenda_id = t.fazenda_id
FROM erp.talhoes t WHERE t.id = os.talhao_id AND os.fazenda_id IS NULL;

ALTER TABLE erp.talhoes        ALTER COLUMN fazenda_id SET NOT NULL;
ALTER TABLE erp.maquinas       ALTER COLUMN fazenda_id SET NOT NULL;
ALTER TABLE erp.operadores     ALTER COLUMN fazenda_id SET NOT NULL;
ALTER TABLE erp.insumos        ALTER COLUMN fazenda_id SET NOT NULL;
ALTER TABLE erp.ordens_servico ALTER COLUMN fazenda_id SET NOT NULL;

-- Contrato 1: o produto é único dentro da fazenda (cada fazenda tem o seu estoque do mesmo produto).
ALTER TABLE erp.insumos DROP CONSTRAINT IF EXISTS insumos_produto_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_insumos_fazenda_produto ON erp.insumos (fazenda_id, produto);

CREATE INDEX IF NOT EXISTS idx_talhoes_fazenda        ON erp.talhoes (fazenda_id);
CREATE INDEX IF NOT EXISTS idx_maquinas_fazenda       ON erp.maquinas (fazenda_id);
CREATE INDEX IF NOT EXISTS idx_operadores_fazenda     ON erp.operadores (fazenda_id);
CREATE INDEX IF NOT EXISTS idx_ordens_servico_fazenda ON erp.ordens_servico (fazenda_id, inicio_previsto);

-- A OS tem de ser da mesma fazenda do talhão, da máquina, do operador e do insumo. A aplicação
-- valida antes (mensagem clara); este gatilho é a rede de segurança contra dado cruzado.
CREATE OR REPLACE FUNCTION erp.checar_fazenda_da_os() RETURNS trigger AS $$
DECLARE
    outra int;
BEGIN
    -- Sem `fazenda_id`, a OS é da fazenda do talhão (quem insere não precisa informar).
    IF NEW.fazenda_id IS NULL THEN
        SELECT fazenda_id INTO NEW.fazenda_id FROM erp.talhoes WHERE id = NEW.talhao_id;
    END IF;
    SELECT fazenda_id INTO outra FROM erp.talhoes WHERE id = NEW.talhao_id;
    IF outra IS DISTINCT FROM NEW.fazenda_id THEN
        RAISE EXCEPTION 'OS: o talhão % não é da fazenda %', NEW.talhao_id, NEW.fazenda_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.maquina_id IS NOT NULL THEN
        SELECT fazenda_id INTO outra FROM erp.maquinas WHERE id = NEW.maquina_id;
        IF outra IS DISTINCT FROM NEW.fazenda_id THEN
            RAISE EXCEPTION 'OS: a máquina % não é da fazenda %', NEW.maquina_id, NEW.fazenda_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.operador_id IS NOT NULL THEN
        SELECT fazenda_id INTO outra FROM erp.operadores WHERE id = NEW.operador_id;
        IF outra IS DISTINCT FROM NEW.fazenda_id THEN
            RAISE EXCEPTION 'OS: o operador % não é da fazenda %', NEW.operador_id, NEW.fazenda_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    IF NEW.insumo_id IS NOT NULL THEN
        SELECT fazenda_id INTO outra FROM erp.insumos WHERE id = NEW.insumo_id;
        IF outra IS DISTINCT FROM NEW.fazenda_id THEN
            RAISE EXCEPTION 'OS: o insumo % não é da fazenda %', NEW.insumo_id, NEW.fazenda_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_os_mesma_fazenda ON erp.ordens_servico;
CREATE TRIGGER trg_os_mesma_fazenda
    BEFORE INSERT OR UPDATE OF fazenda_id, talhao_id, maquina_id, operador_id, insumo_id
    ON erp.ordens_servico
    FOR EACH ROW EXECUTE FUNCTION erp.checar_fazenda_da_os();
