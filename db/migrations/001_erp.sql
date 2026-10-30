-- 001_erp.sql — Schema do mini-ERP da fazenda simulada (contratos 1, 2, 12, 15 e 16).
CREATE SCHEMA IF NOT EXISTS erp;

CREATE TABLE erp.talhoes (
    id                  serial PRIMARY KEY,
    nome                text NOT NULL,
    area_ha             numeric(10,2) NOT NULL CHECK (area_ha > 0),
    cultura             text NOT NULL,                  -- 'milho' | 'soja'
    data_plantio        date NOT NULL,
    estagio_fenologico  text NOT NULL,                  -- ex.: 'V6', 'R1'
    lat                 numeric(9,6) NOT NULL,
    lon                 numeric(9,6) NOT NULL,
    tipo_solo           text
);

CREATE TABLE erp.insumos (
    id              serial PRIMARY KEY,
    produto         text NOT NULL UNIQUE,               -- contrato 1: idêntico a fontes.yaml e Trecho.produto no Qdrant
    tipo            text NOT NULL                       -- contrato 15 / D2: foco só em defensivos agrícolas
        CHECK (tipo IN ('fungicida', 'herbicida', 'inseticida')),
    unidade         text NOT NULL                       -- mesma unidade usada em ordens_servico.dose_por_ha
        CHECK (unidade IN ('L', 'kg')),
    preco_unitario  numeric(12,2) NOT NULL CHECK (preco_unitario >= 0),
    estoque         numeric(12,2) NOT NULL CHECK (estoque >= 0),
    bula_id         text                                -- contrato 1: igual a conhecimento.documentos.id
);

CREATE TABLE erp.maquinas (
    id      serial PRIMARY KEY,
    nome    text NOT NULL,
    tipo    text NOT NULL,                              -- ex.: 'pulverizador'
    status  text NOT NULL DEFAULT 'disponivel'
        CHECK (status IN ('disponivel', 'manutencao'))
);

CREATE TABLE erp.operadores (
    id                serial PRIMARY KEY,
    nome              text NOT NULL,
    telegram_chat_id  bigint
);

CREATE TABLE erp.ordens_servico (
    id               serial PRIMARY KEY,
    talhao_id        int NOT NULL REFERENCES erp.talhoes(id),
    tipo_operacao    text NOT NULL
        CHECK (tipo_operacao <> 'adubacao'),            -- D2: adubação fora do escopo
    insumo_id        int REFERENCES erp.insumos(id),
    dose_por_ha      numeric(10,3)
        CHECK (dose_por_ha IS NULL OR dose_por_ha > 0), -- mesma unidade de insumos.unidade
    inicio_previsto  timestamptz NOT NULL,
    duracao_horas    numeric(5,1) NOT NULL DEFAULT 8
        CHECK (duracao_horas > 0),                      -- contrato 16: fim = inicio_previsto + duracao_horas
    maquina_id       int REFERENCES erp.maquinas(id),
    operador_id      int REFERENCES erp.operadores(id),
    status           text NOT NULL DEFAULT 'agendada'
        CHECK (status IN ('agendada', 'suspensa_preventiva', 'reagendada', 'executada', 'cancelada'))
);

CREATE INDEX idx_ordens_servico_janela
    ON erp.ordens_servico (inicio_previsto, talhao_id);

CREATE TABLE erp.leituras_solo (
    talhao_id    int NOT NULL REFERENCES erp.talhoes(id),
    ts           timestamptz NOT NULL,
    umidade_pct  numeric(5,2) NOT NULL CHECK (umidade_pct >= 0 AND umidade_pct <= 100),
    PRIMARY KEY (talhao_id, ts)
);

