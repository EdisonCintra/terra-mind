-- 004_whatsapp.sql — Frente do Felipe (faixa 003–009): WhatsApp como canal (F4).
-- Idempotente, como as demais.

-- Telefone no formato internacional, só dígitos (ex.: 5564999998888). Mesmo papel do telegram_chat_id.
ALTER TABLE erp.gerentes   ADD COLUMN IF NOT EXISTS whatsapp_telefone bigint;
ALTER TABLE erp.operadores ADD COLUMN IF NOT EXISTS whatsapp_telefone bigint;

-- Janela de 24 h do WhatsApp: a Meta só deixa mandar mensagem livre (texto e botões) dentro de 24 h
-- da última mensagem RECEBIDA do contato; fora dela, só modelo (template) aprovado. O canal grava aqui
-- o momento da última mensagem de cada telefone para escolher entre as duas formas.
CREATE TABLE IF NOT EXISTS agente.whatsapp_janelas (
    telefone           bigint PRIMARY KEY,
    ultima_mensagem_em timestamptz NOT NULL
);
