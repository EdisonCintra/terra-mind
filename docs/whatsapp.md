# WhatsApp como canal (F4), pelo Twilio

`CHANNEL=whatsapp` troca o Telegram pelo WhatsApp via **Twilio** (a API direta da Meta foi retirada). Os nós do
`agents` não mudam: o canal implementa a mesma interface (`channels/base.py`). O código está em
[channels/whatsapp.py](../channels/whatsapp.py).

## Como funciona

| | Telegram | WhatsApp (Twilio) |
|---|---|---|
| Resposta do gerente | polling dentro do `agents` | **webhook HTTPS público** `POST /whatsapp/webhook`, com `X-Twilio-Signature` |
| Mensagem fora de conversa | livre | só **modelo (Content Template) aprovado** se passaram 24 h da última mensagem do contato |
| Custo | grátis | tarifa do Twilio **mais** a taxa da Meta por mensagem (ver a tabela vigente dos dois antes de fechar o BMC) |
| Botões | 3 botões inline | 3 botões de resposta rápida do modelo, ou o gerente escreve `SIM`, `OPCOES` ou `NAO` |

- **Dentro da janela de 24 h:** se `TWILIO_CONTENT_SID_APROVACAO` está definido, o plano vai no modelo de botões
  (Aceitar Proposta, Ver próxima opção, Recusar Proposta); senão vai como texto terminando com "Responda SIM, OPCOES
  ou NAO". Plano com mais de 1024 caracteres vai antes como texto.
- **Fora da janela:** só o modelo aprovado (`TWILIO_CONTENT_SID_APROVACAO` ou `..._AVISO`). Sem ele, o envio
  falha com um erro que explica isso. O canal guarda a última mensagem recebida de cada telefone em
  `agente.whatsapp_janelas`; se o Twilio recusar a mensagem livre por janela fechada (erro 63016), cai para o modelo.
- **Resposta:** o botão volta com o identificador fixo (`SIM`, `OPCOES`, `NAO`), sem o plano. O canal grava o SID de
  cada mensagem de aprovação em `agente.whatsapp_mensagens` e, no clique, acha o plano pela mensagem respondida
  (`OriginalRepliedMessageSid`) ou, na falta dela, pelo plano mais recente do telefone ainda sem resposta. Escrever
  `SIM`, `não`, `opções` vale igual ao clique, **desde que haja plano esperando**; senão é pergunta à bula.
- **Quem aprova:** só o telefone de um gerente da fazenda do incidente (`erp.gerentes.whatsapp_telefone`, cadastrado
  em `/usuarios`). O segundo clique responde "Decisão já registrada". O nome do perfil do WhatsApp vai em
  `respondido_por`.
- **Destino:** cada mensagem vai aos gerentes ativos da fazenda do incidente. `WHATSAPP_GERENTE_TELEFONE` é só o padrão.
- **Pergunte à bula:** texto de um gerente (ou de um número em `WHATSAPP_NUMEROS_BULA`) é uma pergunta à bula.
- **Operador:** o aviso de OS remarcada vai ao `erp.operadores.whatsapp_telefone`.

## Testar já, com a conta gratuita (Sandbox) e o seu número

A conta **trial** do Twilio **não** permite número próprio de WhatsApp nem modelo próprio: isso exige conta paga e
o cadastro na Meta. O que a conta gratuita permite é o **Sandbox do WhatsApp**: um número compartilhado do Twilio
(hoje `+1 415 523 8886`, confira no console) que conversa com **o seu telefone**, depois de você "entrar" nele.
Serve para testar o fluxo inteiro. Limites do trial: cerca de 100 mensagens de WhatsApp, só para números verificados
(até 5) e a sessão do Sandbox vence em 3 dias (é só mandar o `join` de novo).

1. **Conta.** Crie a conta em twilio.com, verifique o seu telefone e anote, em *Account Info* do console:
   **Account SID** → `TWILIO_ACCOUNT_SID` e **Auth Token** → `TWILIO_AUTH_TOKEN`.
2. **Entrar no Sandbox.** No console: *Messaging → Try it out → Send a WhatsApp message*. Ele mostra o número do
   Sandbox e uma palavra (`join <duas-palavras>`). No **seu WhatsApp**, mande essa mensagem para o número. Ele
   responde que você entrou. O número do Sandbox, em dígitos, vai em `TWILIO_WHATSAPP_FROM` (ex.: `+14155238886`).
3. **URL pública do `agents`.** O Twilio precisa chamar o seu computador:

   ```bash
   ngrok http 8000        # ou: cloudflared tunnel --url http://localhost:8000
   ```

   Copie a URL `https://....` que ele mostra.
4. **Webhook no Sandbox.** No console, *Sandbox settings* → campo **"When a message comes in"**:
   `https://<sua-url>/whatsapp/webhook`, método **POST**. Salve. (É por aqui que o clique, o `SIM` e o `join` chegam
   ao TerraMind; o `join` abre a janela de 24 h.)
5. **Seu telefone no banco** (só dígitos com país e DDD, sem `+`). Pelo painel, em `/usuarios`, no seu usuário
   gerente (campo "Telefone do WhatsApp"), ou:

   ```sql
   UPDATE erp.gerentes SET whatsapp_telefone = 5564999998888 WHERE fazenda_id = 1;
   ```

6. **`.env`** (veja `.env.example`) e suba o `agents`:

   ```
   CHANNEL=whatsapp
   TWILIO_ACCOUNT_SID=AC...
   TWILIO_AUTH_TOKEN=...
   TWILIO_WHATSAPP_FROM=+14155238886
   ```

   ```bash
   docker compose up -d --build agents
   ```

   Se o `agents` está atrás do túnel, a assinatura do Twilio é conferida com a URL que o túnel informa. Se der
   `assinatura_invalida` nos logs (`docker compose logs agents`), defina `TWILIO_WEBHOOK_URL` com **exatamente** a
   URL do passo 4.
7. **Teste.**
   - Só o envio: `uv run python -m channels.teste_whatsapp 5564999998888` (o `agents` não precisa estar no ar para
     enviar, mas precisa estar para receber a resposta).
   - Ponta a ponta: no painel, **Simular chuva**. O plano chega ao seu WhatsApp. Responda `SIM` (ou toque no botão,
     se criou o modelo) e confira no painel que o plano foi aceito e que a auditoria mostra o seu nome.

No Sandbox, deixe `TWILIO_CONTENT_SID_*` vazio no primeiro teste: a aprovação vai como texto e você responde
escrevendo. Se quiser ver os botões, rode `uv run python -m channels.twilio_modelos criar` e ponha os dois
`ContentSid` no `.env`; dentro da janela de 24 h o modelo de botões funciona sem aprovação da Meta (se a conta
trial recusar criar o modelo, o script mostra o erro e o texto continua valendo).

## Produção (número próprio): precisa de conta paga

1. Promova a conta do Twilio para paga (*Upgrade*).
2. Registre um **remetente do WhatsApp** (*Messaging → Senders → WhatsApp senders*): o número tem de ser dedicado
   (sem estar no app do WhatsApp comum) e o fluxo passa pelo **Meta Business Manager e pela verificação da empresa**,
   que é a parte que leva dias.
3. Crie os modelos e peça a aprovação: `uv run python -m channels.twilio_modelos criar` e depois
   `uv run python -m channels.twilio_modelos aprovar HX... terramind_aprovacao` (e o mesmo para o de aviso).
   Idioma `pt_BR`, categoria *utility*. Só com modelo aprovado dá para falar fora da janela de 24 h.
4. Troque `TWILIO_WHATSAPP_FROM` pelo seu número e aponte o webhook do **remetente** (não o do Sandbox) para
   `https://<domínio>/whatsapp/webhook`. Em produção use um domínio fixo e `TWILIO_WEBHOOK_URL`.

## Variáveis de ambiente

| Variável | Obrigatória | Para quê |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | sim | identifica a conta (`AC...`) |
| `TWILIO_AUTH_TOKEN` | sim | autentica o envio **e** confere a assinatura do webhook; sem ele o webhook recusa tudo (403) |
| `TWILIO_WHATSAPP_FROM` | sim | número que envia: o do Sandbox ou o seu remetente (`+14155238886`) |
| `TWILIO_CONTENT_SID_APROVACAO` | não | modelo com os 3 botões (`HX...`); sem ele a aprovação vai como texto |
| `TWILIO_CONTENT_SID_AVISO` | não | modelo de aviso simples (`HX...`); necessário só para falar fora da janela de 24 h |
| `TWILIO_WEBHOOK_URL` | não | URL exata que o Twilio chama; vazio = o `agents` usa a que o túnel ou proxy informa |
| `TWILIO_API_URL` | não | só para testes (`https://api.twilio.com`) |
| `WHATSAPP_GERENTE_TELEFONE` | não | gerente padrão, para fazenda sem gerente com telefone |
| `WHATSAPP_NUMEROS_BULA` | não | outros números que podem perguntar à bula (`5564...,5511...`) |

## Modelos (Content Templates)

Criados por `python -m channels.twilio_modelos criar`:

- **`terramind_aprovacao`** (`twilio/quick-reply`): corpo `{{1}}` e três botões com identificadores fixos:
  `SIM` ("Aceitar Proposta"), `OPCOES` ("Ver próxima opção") e `NAO` ("Recusar Proposta").
- **`terramind_aviso`** (`twilio/text`): corpo `{{1}}`.

`{{1}}` recebe o texto do plano ou do alerta. Para o modelo **aprovado** (fora da janela), o canal troca as quebras de
linha por " · " (a Meta não aceita quebra nem 4+ espaços numa variável) e corta em 1000 caracteres; dentro da janela o
texto vai inteiro (até 1024 no modelo de botões).

## Testes automáticos

`uv run pytest tests/test_channels_whatsapp.py`: o Twilio é simulado (`httpx.MockTransport`), com o Postgres real.
Cobrem a janela de 24 h, o envio com e sem modelo, o clique achando o plano, o texto `SIM`/`NAO`, a assinatura, a
URL assinada atrás de proxy e o destino por fazenda.

## O que ainda não foi validado contra o Twilio real

Foi escrito a partir da documentação do Twilio e testado contra uma API simulada. Os pontos a conferir na primeira
rodada real: os campos do webhook (`ButtonPayload`, `OriginalRepliedMessageSid`, `ProfileName`), o código de erro de
janela fechada (63016, que também pode chegar depois, no status da mensagem, e não na resposta do envio), o formato
do pedido de criação e de aprovação dos modelos na Content API e a conferência da assinatura atrás do túnel.
Se algo divergir, os logs do `agents` dizem o que chegou.
