-- 900_seed.sql — Fazenda simulada para o cenário da demo (contratos 1, 12 e 15; D2).
-- Datas sempre relativas a `now()` para funcionar em qualquer dia de ensaio ou apresentação.
SET TIME ZONE 'America/Sao_Paulo';

-- Coordenadas reais da região de Rio Verde - GO (usadas pelo Monitor no modo live).
INSERT INTO erp.talhoes (nome, area_ha, cultura, data_plantio, estagio_fenologico, lat, lon, tipo_solo) VALUES
    ('Talhão 1 - Sede',    120.00, 'milho', current_date - 30, 'V6', -17.790000, -50.920000, 'latossolo'),
    ('Talhão 2 - Baixada',  85.50, 'soja',  current_date - 45, 'R1', -17.810000, -50.900000, 'latossolo');

-- Só defensivos agrícolas (D2 / contrato 15).
-- Contrato 1: `produto` e `bula_id` iguais ao `fontes.yaml` do Edison (`id: bula-abacus-hc`, `produto: Abacus HC`).
-- Engeo Pleno S não tem bula no `fontes.yaml` (e não há fator de perda para inseticida): `bula_id` NULL; só aparece na OS 3, fora da janela de risco.
INSERT INTO erp.insumos (produto, tipo, unidade, preco_unitario, estoque, bula_id) VALUES
    ('Abacus HC',     'fungicida',  'L', 185.00, 500.00, 'bula-abacus-hc'),
    ('Engeo Pleno S', 'inseticida', 'L', 210.00, 300.00, NULL);

-- Dois pulverizadores disponíveis: A atende a OS agendada; B está totalmente livre para qualquer janela de remarcação.
INSERT INTO erp.maquinas (nome, tipo, status) VALUES
    ('Pulverizador A', 'pulverizador', 'disponivel'),
    ('Pulverizador B', 'pulverizador', 'disponivel');

-- Preencha `telegram_chat_id` no seu ambiente de teste para receber o aviso do operador no Telegram.
INSERT INTO erp.operadores (nome, telegram_chat_id) VALUES
    ('João',   NULL),
    ('Carlos', NULL);

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

-- Leituras horárias simuladas de umidade do solo nas últimas 48 h para os dois talhões.
INSERT INTO erp.leituras_solo (talhao_id, ts, umidade_pct)
SELECT
    t.id,
    date_trunc('hour', now()) - (h || ' hours')::interval,
    round((28 + (random() * 4))::numeric, 2)
FROM erp.talhoes t
CROSS JOIN generate_series(0, 47) AS h;

