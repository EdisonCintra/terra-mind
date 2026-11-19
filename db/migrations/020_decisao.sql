-- 020_decisao.sql — Frente do Gustavo (faixa 020–029, contrato 29): impactos, limites por produto,
-- execução da OS e relatório de aplicação (contratos 25, 26, 27 e 28).
-- Idempotente (IF NOT EXISTS): enquanto o runner de migrations do F2 não existe, um banco já criado
-- recebe este arquivo com `psql -f db/migrations/020_decisao.sql`, sem `docker compose down -v`.

-- Contrato 27: incidente de replay não entra no histórico nem no dashboard de perda.
ALTER TABLE agente.incidentes
    ADD COLUMN IF NOT EXISTS origem text NOT NULL DEFAULT 'live'
        CHECK (origem IN ('live', 'replay'));

-- Contrato 25: uma linha por OS suspensa num incidente. Os valores em R$ saem de código
-- (`agents.rules.impacto`); o `acao_preventiva` grava e o `executor`, o `encerrar` e o
-- `decisao_manual` atualizam a situação.
CREATE TABLE IF NOT EXISTS agente.impactos (
    id                  serial PRIMARY KEY,
    incidente_id        int NOT NULL REFERENCES agente.incidentes(id) ON DELETE CASCADE,
    os_id               int NOT NULL REFERENCES erp.ordens_servico(id) ON DELETE CASCADE,
    talhao_id           int NOT NULL REFERENCES erp.talhoes(id) ON DELETE CASCADE,
    produto             text,
    condicao            text NOT NULL,                      -- parâmetro violado: 'chuva', 'vento'...
    valor_aplicacao_rs  numeric(14,2),                      -- área × dose × preço
    fator               numeric CHECK (fator IS NULL OR fator BETWEEN 0 AND 1),
    fonte               text NOT NULL,                      -- "<doc_id>:p.<N>" ou "estimativa: ..."
    perda_rs            numeric(14,2),                      -- NULL = sem fator: nunca um valor inventado
    situacao            text NOT NULL DEFAULT 'suspensa'
        CHECK (situacao IN ('suspensa', 'evitada', 'recusada', 'decisao_manual')),
    plano_id            int REFERENCES agente.planos(id) ON DELETE SET NULL,
    criado_em           timestamptz NOT NULL DEFAULT now(),
    atualizado_em       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (incidente_id, os_id)
);

CREATE INDEX IF NOT EXISTS idx_impactos_criado_em ON agente.impactos (criado_em);

-- Contrato 26 (D6): limites lidos uma vez de cada bula, com trecho literal e página. A agenda e a
-- avaliação de janela usam estes limites antes de existir qualquer incidente.
CREATE TABLE IF NOT EXISTS conhecimento.limites_produto (
    id           serial PRIMARY KEY,
    produto      text NOT NULL,                             -- = erp.insumos.produto (contrato 1)
    parametro    text NOT NULL
        CHECK (parametro IN ('chuva', 'vento', 'umidade', 'temperatura', 'rajada', 'delta_t', 'inversao')),
    sentido      text NOT NULL CHECK (sentido IN ('maximo', 'minimo')),
    limite       numeric NOT NULL,
    unidade      text,
    janela_h     numeric CHECK (janela_h IS NULL OR (janela_h > 0 AND janela_h <= 120)),
    trecho       text NOT NULL,
    documento    text NOT NULL,                             -- = conhecimento.documentos.id
    pagina       int NOT NULL,
    extraido_em  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (produto, parametro, sentido)
);

-- Contrato 28: dados reais da aplicação. Os campos de voo só valem para drone (Portaria MAPA
-- 298/2021, art. 10) e avião (IN MAPA 2/2008, art. 9º).
CREATE TABLE IF NOT EXISTS erp.execucoes_os (
    os_id                 int PRIMARY KEY REFERENCES erp.ordens_servico(id) ON DELETE CASCADE,
    inicio_real           timestamptz NOT NULL,
    fim_real              timestamptz NOT NULL,
    area_aplicada_ha      numeric(10,2) NOT NULL CHECK (area_aplicada_ha > 0),
    dose_real_por_ha      numeric(10,3) CHECK (dose_real_por_ha IS NULL OR dose_real_por_ha > 0),
    volume_calda_l_ha     numeric(10,2) CHECK (volume_calda_l_ha IS NULL OR volume_calda_l_ha > 0),
    modalidade            text NOT NULL DEFAULT 'terrestre'
        CHECK (modalidade IN ('terrestre', 'drone', 'aerea')),
    maquina_id            int REFERENCES erp.maquinas(id),
    operador_id           int REFERENCES erp.operadores(id),
    receituario_numero    text,
    responsavel_tecnico   text,
    registro_profissional text,                             -- CREA do responsável técnico
    altura_voo_m          numeric(6,2),
    largura_faixa_m       numeric(6,2),
    aeronave              text,                             -- identificação da ARP (ANAC) ou prefixo
    ponta                 text,                             -- tipo/modelo da ponta de pulverização
    observacoes           text,
    registrado_por        text NOT NULL DEFAULT 'painel',
    registrado_em         timestamptz NOT NULL DEFAULT now(),
    CHECK (fim_real > inicio_real)
);

-- G7: o PDF e os dados que o geraram. O `sha256` é do JSON dos dados: qualquer alteração posterior
-- aparece. Gerar de novo cria outra versão; a anterior fica guardada.
CREATE TABLE IF NOT EXISTS agente.relatorios_aplicacao (
    id         serial PRIMARY KEY,
    os_id      int NOT NULL REFERENCES erp.ordens_servico(id) ON DELETE CASCADE,
    versao     int NOT NULL DEFAULT 1,
    gerado_em  timestamptz NOT NULL DEFAULT now(),
    conforme   boolean,                                     -- NULL = sem clima ou sem limite para conferir
    dados      jsonb NOT NULL,
    sha256     text NOT NULL,
    pdf        bytea NOT NULL,
    UNIQUE (os_id, versao)
);
