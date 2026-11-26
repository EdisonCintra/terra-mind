# Como rodar o TerraMind e criar a sua conta

Guia curto para testar o sistema na sua máquina. Cada pessoa roda o próprio ambiente, com banco e usuários próprios.

## 1. Pré-requisitos

- Git.
- Docker Desktop aberto e com o motor rodando.
- Uma chave da OpenAI (sem ela o sistema sobe, mas os agentes não montam o plano).

Os comandos abaixo são de PowerShell, na pasta do projeto.

## 2. Baixar o código

```powershell
git fetch origin
git checkout fix/telegram-subida
git pull
```

Enquanto o PR não entra no `main`, use a branch `fix/telegram-subida`: ela tem do F1 ao F6 e as correções posteriores.

## 3. Configurar o `.env`

Se você ainda não tem um `.env`:

```powershell
Copy-Item .env.example .env
```

Preencha e salve:

```
OPENAI_API_KEY=sua-chave
CHANNEL=painel
```

- `CHANNEL=painel` aprova os planos pela própria tela, sem Telegram nem WhatsApp. **Não use o mesmo bot do
  Telegram em duas máquinas ao mesmo tempo.**
- **Se você já tinha um `.env`:** variável vazia não pode ter comentário na mesma linha (`NOME=    # texto`), porque o
  Docker lê o comentário como valor. Apague o comentário ou mova-o para a linha de cima. As variáveis `WHATSAPP_*` da
  Meta não são mais usadas.
- Opcionais: `AGENTS_TOKEN` (protege a aprovação; o mesmo `.env` vale para o painel e o `agents`) e
  `AGROFIT_CLIENT_ID` e `AGROFIT_CLIENT_SECRET` (busca de produto no cadastro de insumo).
- Nunca faça commit do `.env`.

## 4. Subir o sistema

```powershell
docker compose up -d --build
docker compose ps -a
```

A primeira vez demora vários minutos (imagens e índice das bulas). As migrations do banco são aplicadas sozinhas.
`migrar` e `conhecimento-carga` terminam com `Exited (0)`, e isso é o normal; os demais ficam `Up`.

## 5. Criar a sua conta e entrar

Com o sistema no ar, crie a conta de **gerente** pelo terminal. Não precisa colocar e-mail nem senha no `.env`.

```powershell
docker compose exec painel uv run --no-sync python -m painel.criar_usuario --nome Fellipe --email terramind404@gmail.com
```

- Para usar outro nome ou e-mail, troque os valores (nome com espaço vai entre aspas: `--nome "Maria Silva"`).
- O comando pede a **senha**. Nada aparece enquanto você digita; aperte Enter ao terminar.
- A senha precisa de 8 caracteres ou mais, não pode ser só números e não pode começar ou terminar com espaço.
- A resposta esperada é `Gerente criado (id N). Entre em /login.`
- Cada e-mail só pode ser cadastrado uma vez no mesmo banco.

Depois abra <http://localhost:8080/login> e entre com o e-mail e a senha escolhidos.

**Usuário operador** (para testar a visão do campo): entre como gerente, abra **Usuários**, clique em **Novo usuário**,
escolha o perfil *Operador*, marque a fazenda e selecione o **Operador de campo**.

## 6. O que testar

| Onde | O que fazer | Perfil |
|---|---|---|
| Visão geral | **Simular chuva** e aprovar o plano que aparecer | gerente |
| Cadastros | criar talhão (ponto ou polígono no mapa), máquina, operador, insumo e OS | gerente |
| Fazenda, Mapa, Agenda, Perda evitada, Histórico | conferir os dados da fazenda | os dois |
| Usuários | criar um operador | gerente |
| Executar OS (`/campo`) | registrar a execução de uma OS, de preferência no celular | operador |
| Relatórios | abrir o PDF da aplicação concluída | os dois |

O operador não vê Cadastros nem Usuários e não aprova planos: isso é o esperado.

No celular, na mesma rede Wi-Fi: `http://<IP-do-seu-PC>:8080` (o firewall do Windows pode pedir para liberar a porta).

WhatsApp é um teste à parte: [whatsapp.md](whatsapp.md).

## 7. Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Erro do Docker no meio do build (`EOF`, `Unavailable`) | reinicie o Docker Desktop e rode o `up` de novo |
| Um serviço não fica `Up` | `docker compose logs --tail 40 <serviço>` |
| Mudou o `.env` e nada aconteceu | `docker compose up -d` (recria os serviços afetados) |
| Esqueceu a senha | outro gerente redefine em **Usuários**; sozinho, rode o comando do passo 5 com **outro e-mail**, entre com ele e redefina a senha |
| "Muitas tentativas" no login | espere 15 minutos ou `docker compose restart painel` |
| Simular chuva não gera plano | confira a `OPENAI_API_KEY` e `docker compose logs --tail 40 agents`. Se o banco é antigo, as OS de exemplo já passaram: veja abaixo |

**Recomeçar do zero** (apaga todos os dados, inclusive usuários e cadastros):

```powershell
docker compose down -v
docker compose up -d --build
```

As OS de exemplo voltam com datas a partir de hoje. Como o banco fica vazio de usuários, crie a conta de novo (passo 5).

**Testes automáticos apagam os dados.** `uv run pytest` usa por padrão o mesmo banco do sistema e limpa as tabelas,
inclusive usuários. Para rodar os testes sem perder o que você cadastrou, use um banco separado (precisa do `uv` instalado):

```powershell
docker compose exec postgres createdb -U terramind terramind_test
$env:DATABASE_URL = "postgresql://terramind:terramind@localhost:5432/terramind_test"
uv run python -m db.migrar
uv run pytest
Remove-Item Env:DATABASE_URL
```

O `createdb` só é necessário na primeira vez.

## 8. Avisar o que encontrar

Mande a tela, o que você fez, o que esperava e o log do serviço (`docker compose logs --tail 40 <serviço>`), sem o
conteúdo do `.env`.
