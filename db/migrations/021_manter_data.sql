-- "Manter data original" (resposta NAO do gerente): a OS suspensa volta a `agendada` no horário de antes e o
-- gerente assume o risco do clima. A perda não foi evitada, mas também não é "proposta recusada" com a OS
-- parada: vira a situação `mantida` no dashboard de perda evitada (G1).
ALTER TABLE agente.impactos DROP CONSTRAINT IF EXISTS impactos_situacao_check;
ALTER TABLE agente.impactos ADD CONSTRAINT impactos_situacao_check
    CHECK (situacao IN ('suspensa', 'evitada', 'recusada', 'mantida', 'decisao_manual'));
