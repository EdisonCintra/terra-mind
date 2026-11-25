# WhatsApp como canal (F4)

`CHANNEL=whatsapp` troca o Telegram pelo WhatsApp Cloud API (Meta). Os nós do `agents` não mudam: o canal
implementa a mesma interface (`channels/base.py`). O código está em [channels/whatsapp.py](../channels/whatsapp.py).

## Como funciona

| | Telegram | WhatsApp |
|---|---|---|
| Resposta do gerente | polling dentro do `agents` | **webhook HTTPS público** em `/whatsapp/webhook`, com assinatura da Meta |
| Mensagem fora de conversa | livre | só **modelo (template) aprovado** se passaram 24 h da última mensagem do contato |
| Custo | grátis | **cada modelo enviado é cobrado** pela Meta (custo a levantar no BMC) |
| Botões | 3 botões inline | 3 botões de resposta (dentro das 24 h) ou 3 respostas rápidas do modelo (fora delas) |

- **Dentro da janela de 24 h:** mensagem interativa com os botões Aceitar Proposta, Ver próxima opção e Recusar Proposta
  (rótulos de até 20 caracteres, o limite da Meta). Plano com mais de 1024 caracteres vai antes como texto.
- **Fora da janela:** o modelo `terramind_aprovacao` com as 3 respostas rápidas. O canal guarda a última mensagem
  recebida de cada telefone em `agente.whatsapp_janelas`; se a Meta recusar a mensagem livre (erro 131047), cai para o modelo.
- **Clique:** chega ao webhook, vira a mesma `Resposta` do Telegram e retoma a sessão. O segundo clique responde
  "Decisão já registrada". O nome de quem clicou (perfil do WhatsApp) vai em `respondido_por`.
- **Quem aprova:** só o telefone de um gerente da fazenda do incidente (`erp.gerentes.whatsapp_telefone`).
- **Destino:** cada mensagem vai aos gerentes ativos da fazenda do incidente. `WHATSAPP_GERENTE_TELEFONE` é só o padrão.
- **Pergunte à bula:** texto de um gerente (ou de um número em `WHATSAPP_NUMEROS_BULA`) é uma pergunta à bula.
- **Operador:** o aviso de OS remarcada vai ao `erp.operadores.whatsapp_telefone`.

## Passo a passo na Meta (comece na primeira semana: leva dias)

1. **Conta e app.** Em developers.facebook.com, crie um app do tipo *Business* e adicione o produto *WhatsApp*.
   Anote o **App Secret** (Configurações do app → Básico) → `WHATSAPP_APP_SECRET`.
2. **Número e empresa.** Em *WhatsApp → Configuração da API*, anote o **ID do número de telefone** →
   `WHATSAPP_PHONE_NUMBER_ID`. Para falar com números reais (não só os de teste), é preciso cadastrar o número
   da empresa e passar pela **verificação da empresa** na Meta (o passo que demora). Enquanto isso, o número de
   teste da Meta aceita até 5 destinatários cadastrados.
3. **Token permanente.** O token que aparece na tela de configuração expira em 24 h. Crie um *usuário do sistema*
   no Business Manager, dê acesso ao app e gere um token com `whatsapp_business_messaging` e
   `whatsapp_business_management` → `WHATSAPP_TOKEN`.
4. **Modelos de mensagem** (seção abaixo): crie e espere a aprovação.
5. **Webhook.** O `agents` precisa de uma URL pública em HTTPS: `https://<seu-domínio>/whatsapp/webhook`.
   Em desenvolvimento use um túnel (`cloudflared tunnel --url http://localhost:8000` ou `ngrok http 8000`).
   Em *WhatsApp → Configuração → Webhook*, informe a URL e um **token de verificação** qualquer que você inventa
   (o mesmo em `WHATSAPP_VERIFY_TOKEN`) e assine o campo **messages**.
6. **Telefones no banco.** Telefone no formato internacional, só dígitos (ex.: `5564999998888`):

   ```sql
   UPDATE erp.gerentes   SET whatsapp_telefone = 5564999998888 WHERE fazenda_id = 1;
   UPDATE erp.operadores SET whatsapp_telefone = 5564999997777 WHERE nome = 'João';
   ```

7. **`.env`:** `CHANNEL=whatsapp` e as variáveis da tabela abaixo; depois `docker compose up -d --build agents`.

## Variáveis de ambiente

| Variável | Obrigatória | Para quê |
|---|---|---|
| `WHATSAPP_TOKEN` | sim | token de acesso da Cloud API |
| `WHATSAPP_PHONE_NUMBER_ID` | sim | ID do número que envia |
| `WHATSAPP_APP_SECRET` | sim, para receber | confere a assinatura `X-Hub-Signature-256`; sem ele o webhook recusa tudo (403) |
| `WHATSAPP_VERIFY_TOKEN` | sim, para cadastrar o webhook | token da verificação (GET) |
| `WHATSAPP_GERENTE_TELEFONE` | não | gerente padrão, para fazenda sem gerente com telefone |
| `WHATSAPP_NUMEROS_BULA` | não | outros números que podem perguntar à bula (`5564...,5511...`) |
| `WHATSAPP_TEMPLATE_APROVACAO` | não (`terramind_aprovacao`) | nome do modelo com os 3 botões |
| `WHATSAPP_TEMPLATE_AVISO` | não (`terramind_aviso`) | nome do modelo de aviso simples |
| `WHATSAPP_TEMPLATE_IDIOMA` | não (`pt_BR`) | idioma dos modelos |
| `WHATSAPP_API_VERSION` | não (`v22.0`) | versão da Graph API; confira a vigente no painel da Meta |

## Modelos a cadastrar (WhatsApp Manager → Modelos de mensagem)

Idioma **Português (BR)**, categoria **Utilidade**. A variável `{{1}}` recebe o texto do plano ou do alerta; o canal
troca as quebras de linha por " · " (a Meta não aceita quebra nem 4+ espaços numa variável) e corta em 1000 caracteres.
A Meta pede um exemplo para a variável: use um plano real encurtado.

**`terramind_aprovacao`** (3 botões de resposta rápida, nesta ordem):

- Corpo: `TerraMind, aviso para o gerente da fazenda: {{1}} Responda com um dos botões abaixo.`
- Botões: `Aceitar Proposta`, `Ver próxima opção`, `Recusar Proposta`

**`terramind_aviso`** (sem botões):

- Corpo: `TerraMind, aviso da fazenda: {{1}} Mais detalhes no painel.`

Os nomes precisam ser iguais aos de `WHATSAPP_TEMPLATE_*`. O texto antes e depois da variável é exigência da Meta
(o modelo não pode começar nem terminar numa variável).

## Testar

1. Testes automáticos (Cloud API simulada): `uv run pytest tests/test_channels_whatsapp.py`.
2. Contra a Meta de verdade, depois de cadastrar o número de teste como destinatário:

   ```bash
   uv run python -m channels.teste_whatsapp 5564999998888
   ```

   Manda um plano de exemplo com os botões; clique e a resposta aparece no terminal. Para o webhook funcionar nesse
   teste, o `agents` precisa estar no ar com `CHANNEL=whatsapp` (é ele que recebe o clique); o script só envia.
3. Ponta a ponta: **Simular chuva** no painel e confira a mensagem no WhatsApp com os 3 botões.

## O que não foi validado contra a Meta

O canal foi testado contra uma Cloud API simulada (formato das mensagens, janela de 24 h, assinatura, destino por fazenda).
Ainda precisa de uma rodada real depois da conta, do número e dos modelos aprovados: o formato exato do JSON dos modelos
e a versão da Graph API são os pontos a conferir.
