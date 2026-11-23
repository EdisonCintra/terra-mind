-- Registro das bulas no banco (E5): o banco passa a ser a fonte dos documentos indexados.
-- `arquivo` é relativo a BULAS_DIR (e, na falta dele, à pasta base_agricola/dados/docs).
-- `sha256` + `indexado_em` deixam a indexação reprocessar só PDF novo ou alterado.
ALTER TABLE conhecimento.documentos ADD COLUMN IF NOT EXISTS registro_mapa text;
ALTER TABLE conhecimento.documentos ADD COLUMN IF NOT EXISTS titular       text;
ALTER TABLE conhecimento.documentos ADD COLUMN IF NOT EXISTS sha256       text;
ALTER TABLE conhecimento.documentos ADD COLUMN IF NOT EXISTS indexado_em   timestamptz;

-- Uma bula por produto (contrato 1: erp.insumos.produto).
CREATE UNIQUE INDEX IF NOT EXISTS documentos_bula_produto_uq
    ON conhecimento.documentos (produto) WHERE tipo_doc = 'bula';

-- Bulas baixadas da ADAPAR (cópia das aprovadas pelo MAPA) em 30/10/2026;
-- fenologia baixada da Embrapa em 31/10/2026 (sem produto: a busca por produto não traz esses trechos).
INSERT INTO conhecimento.documentos (id, titulo, tipo_doc, produto, culturas, url, arquivo) VALUES
    ('bula-abacus-hc', 'Bula Abacus HC (BASF, reg. MAPA 9210)', 'bula', 'Abacus HC', '{soja,milho}',
     'https://www.adapar.pr.gov.br/sites/adapar/arquivos_restritos/files/documento/2026-08/abacus_hc.pdf', 'abacus_hc.pdf'),
    ('bula-ativum', 'Bula Ativum (BASF, reg. MAPA 11216)', 'bula', 'Ativum', '{soja}',
     'https://www.adapar.pr.gov.br/sites/adapar/arquivos_restritos/files/documento/2026-07/ativum.pdf', 'ativum.pdf'),
    ('bula-brucia', 'Bula Brucia (ISK)', 'bula', 'Brucia', '{milho,soja}',
     'https://www.adapar.pr.gov.br/sites/adapar/arquivos_restritos/files/documento/2026-08/brucia.pdf', 'brucia.pdf'),
    ('bula-amplexus', 'Bula Amplexus (BASF)', 'bula', 'Amplexus', '{soja}',
     'https://www.adapar.pr.gov.br/sites/adapar/arquivos_restritos/files/documento/2026-07/amplexus.pdf', 'amplexus.pdf'),
    ('fenologia-milho', 'Fisiologia do Milho (Embrapa, Circular Técnica 22, 2002)', 'fenologia', NULL, '{milho}',
     'https://www.infoteca.cnptia.embrapa.br/bitstream/doc/486995/1/Circ22.pdf', 'fenologia_milho.pdf'),
    ('fenologia-soja', 'Estádios fenológicos da soja (Embrapa)', 'fenologia', NULL, '{soja}',
     'https://www.alice.cnptia.embrapa.br/alice/bitstream/doc/1128387/1/p.-33-54-de-SP-17-2020-online.pdf', 'fenologia_soja.pdf')
ON CONFLICT (id) DO NOTHING;
