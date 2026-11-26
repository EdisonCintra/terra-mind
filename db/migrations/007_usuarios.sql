-- 007_usuarios.sql — Frente do Felipe (faixa 003–009, contrato 23): login e perfis do painel (F3).
-- Usuário pertence a uma organização, enxerga as fazendas ligadas a ele e tem um papel (gerente ou operador).
-- A senha nunca é guardada: só o hash (scrypt, ver painel/auth.py). A sessão do navegador é um token
-- aleatório; o banco guarda só o hash do token, então um vazamento da tabela não entrega sessões.
-- Idempotente: pode rodar de novo sem apagar nada.

CREATE TABLE IF NOT EXISTS erp.usuarios (
    id                 serial PRIMARY KEY,
    organizacao_id     int NOT NULL REFERENCES erp.organizacoes(id),
    nome               text NOT NULL,
    email              text NOT NULL,
    senha_hash         text NOT NULL,
    papel              text NOT NULL CHECK (papel IN ('gerente', 'operador')),
    telegram_chat_id   bigint,
    whatsapp_telefone  bigint,
    operador_id        int REFERENCES erp.operadores(id),   -- quem é o operador de campo (F6)
    ativo              boolean NOT NULL DEFAULT true,
    criado_em          timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_usuarios_email ON erp.usuarios (lower(email));

-- Fazendas que o usuário enxerga (todas dentro da organização dele; o painel confere).
CREATE TABLE IF NOT EXISTS erp.usuario_fazendas (
    usuario_id  int NOT NULL REFERENCES erp.usuarios(id) ON DELETE CASCADE,
    fazenda_id  int NOT NULL REFERENCES erp.fazendas(id) ON DELETE CASCADE,
    PRIMARY KEY (usuario_id, fazenda_id)
);

CREATE TABLE IF NOT EXISTS erp.sessoes (
    token_hash  text PRIMARY KEY,
    usuario_id  int NOT NULL REFERENCES erp.usuarios(id) ON DELETE CASCADE,
    criada_em   timestamptz NOT NULL DEFAULT now(),
    expira_em   timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessoes_usuario ON erp.sessoes (usuario_id);

-- O gerente de uma fazenda que tem usuário passa a ser mantido por ele: o painel grava aqui as linhas que os
-- canais (Telegram e WhatsApp) já leem para saber quem aprova. Gerente sem `usuario_id` (seed, .env) segue valendo.
ALTER TABLE erp.gerentes ADD COLUMN IF NOT EXISTS usuario_id int REFERENCES erp.usuarios(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_gerentes_usuario ON erp.gerentes (usuario_id) WHERE usuario_id IS NOT NULL;
