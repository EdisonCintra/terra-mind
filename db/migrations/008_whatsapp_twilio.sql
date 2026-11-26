-- 008_whatsapp_twilio.sql — Frente do Felipe (faixa 003–009): WhatsApp pelo Twilio (F4).
-- Idempotente, como as demais.

-- O botão do modelo do Twilio volta só com o identificador fixo (SIM, OPCOES ou NAO), sem o plano. Por isso o
-- canal guarda o SID de cada mensagem de aprovação que enviou e de qual plano ela é: no clique, o plano sai da
-- mensagem respondida (OriginalRepliedMessageSid) ou, na falta dela, do mais recente ainda sem resposta.
CREATE TABLE IF NOT EXISTS agente.whatsapp_mensagens (
    id            serial PRIMARY KEY,
    sid           text UNIQUE,
    telefone      bigint NOT NULL,
    incidente_id  int NOT NULL,
    plano_id      int NOT NULL,
    criada_em     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_mensagens_telefone ON agente.whatsapp_mensagens (telefone, criada_em DESC);
