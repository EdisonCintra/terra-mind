-- E7: conhecimento.fatores_perda passa a aceitar a condição 'atraso' (fração perdida por dia de demora).
-- Idempotente: recria o CHECK criado inline em 010_conhecimento.sql.
ALTER TABLE conhecimento.fatores_perda DROP CONSTRAINT IF EXISTS fatores_perda_condicao_check;
ALTER TABLE conhecimento.fatores_perda
    ADD CONSTRAINT fatores_perda_condicao_check
    CHECK (condicao IN ('chuva', 'vento', 'umidade', 'temperatura', 'atraso'));
