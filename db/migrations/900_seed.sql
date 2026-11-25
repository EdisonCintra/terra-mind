-- 900_seed.sql — Fazenda simulada para o cenário da demo (contratos 1, 12 e 15; D2).
-- Datas sempre relativas a `now()` para funcionar em qualquer dia de ensaio ou apresentação.
SET TIME ZONE 'America/Sao_Paulo';

-- F2 (contrato 20): duas organizações independentes. A "Fazenda Exemplo" (id 1) tem a operação da demo;
-- a "Fazenda Demo" (id 2, `demo = true`) é onde o replay do Monitor roda sem brigar com incidentes
-- reais (só pode haver um incidente ativo por talhão, contrato 3).
INSERT INTO erp.organizacoes (nome) VALUES ('Organização Exemplo'), ('Organização Demo');
INSERT INTO erp.fazendas (organizacao_id, nome, demo) VALUES
    (1, 'Fazenda Exemplo', false),
    (2, 'Fazenda Demo',    true);

-- Gerentes por fazenda: preencha `telegram_chat_id` no seu ambiente para receber a aprovação no Telegram.
-- Sem chat do gerente da fazenda, o canal usa `GERENTE_CHAT_ID` do `.env` (gerente padrão).
INSERT INTO erp.gerentes (fazenda_id, nome, telegram_chat_id) VALUES
    (1, 'Gerente Exemplo', NULL),
    (2, 'Gerente Demo',    NULL);

-- Coordenadas reais da região de Rio Verde - GO (usadas pelo Monitor no modo live).
INSERT INTO erp.talhoes (fazenda_id, nome, area_ha, cultura, data_plantio, estagio_fenologico, lat, lon, tipo_solo) VALUES
    (1, 'Talhão 1 - Sede',    120.00, 'milho', current_date - 30, 'V6', -17.790000, -50.920000, 'latossolo'),
    (1, 'Talhão 2 - Baixada',  85.50, 'soja',  current_date - 45, 'R1', -17.810000, -50.900000, 'latossolo'),
    (2, 'Talhão Demo',        100.00, 'milho', current_date - 30, 'V6', -17.850000, -50.880000, 'latossolo');

-- F5: o que vai ser plantado (planejamento de safra), datas relativas como o resto do seed.
INSERT INTO erp.planejamento_safra (talhao_id, safra, cultura, plantio_previsto, observacoes) VALUES
    (1, '2026/27', 'soja',  current_date + 150, 'Soja depois da colheita do milho'),
    (2, '2026/27', 'milho', current_date + 90,  'Milho safrinha depois da soja'),
    (3, '2026/27', 'soja',  current_date + 120, NULL);

-- Só defensivos agrícolas (D2 / contrato 15).
-- Contrato 1: `produto` e `bula_id` iguais ao `fontes.yaml` do Edison (`id: bula-abacus-hc`, `produto: Abacus HC`).
-- Engeo Pleno S não tem bula no `fontes.yaml` (e não há fator de perda para inseticida): `bula_id` NULL; só aparece na OS 3, fora da janela de risco.
INSERT INTO erp.insumos (fazenda_id, produto, tipo, unidade, preco_unitario, estoque, bula_id) VALUES
    (1, 'Abacus HC',     'fungicida',  'L', 185.00, 500.00, 'bula-abacus-hc'),
    (1, 'Engeo Pleno S', 'inseticida', 'L', 210.00, 300.00, NULL),
    (2, 'Abacus HC',     'fungicida',  'L', 185.00, 200.00, 'bula-abacus-hc');

-- Dois pulverizadores disponíveis: A atende a OS agendada; B está totalmente livre para qualquer janela de remarcação.
INSERT INTO erp.maquinas (fazenda_id, nome, tipo, status) VALUES
    (1, 'Pulverizador A', 'pulverizador', 'disponivel'),
    (1, 'Pulverizador B', 'pulverizador', 'disponivel'),
    (2, 'Pulverizador Demo', 'pulverizador', 'disponivel');

-- Preencha `telegram_chat_id` no seu ambiente de teste para receber o aviso do operador no Telegram.
INSERT INTO erp.operadores (fazenda_id, nome, telegram_chat_id) VALUES
    (1, 'João',   NULL),
    (1, 'Carlos', NULL),
    (2, 'Operador Demo', NULL);

-- Ordens de serviço:
--   OS 1 (cenário principal): pulverizar Abacus HC (0,3 L/ha) no Talhão 1 daqui a 20 h (cai nos 80 mm / 36 h do replay).
--   OS 2 e OS 3 (fora da janela de risco): daqui a 6 e 8 dias, provando que o sistema filtra só a OS em risco
--   e mantendo ambos os pulverizadores livres nas janelas secas pós-chuva (+36 h a +120 h) sem sobreposição.
INSERT INTO erp.ordens_servico
    (talhao_id, tipo_operacao, insumo_id, dose_por_ha, inicio_previsto, duracao_horas, maquina_id, operador_id, status)
VALUES
    (1, 'pulverizacao', 1, 0.300, date_trunc('hour', now()) + interval '20 hours', 8.0, 1, 1, 'agendada'),
    (2, 'pulverizacao', 1, 0.300, date_trunc('hour', now()) + interval '6 days',   8.0, 1, 1, 'agendada'),
    (1, 'pulverizacao', 2, 0.250, date_trunc('hour', now()) + interval '8 days',   8.0, 2, 2, 'agendada');

-- Leituras horárias simuladas de umidade do solo nas últimas 48 h para os dois talhões da Fazenda Exemplo.
INSERT INTO erp.leituras_solo (talhao_id, ts, umidade_pct)
SELECT
    t.id,
    date_trunc('hour', now()) - (h || ' hours')::interval,
    round((28 + (random() * 4))::numeric, 2)
FROM erp.talhoes t
CROSS JOIN generate_series(0, 47) AS h
WHERE t.fazenda_id = 1;

