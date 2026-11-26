# Pendências do Felipe (atualizado em 25/11/2026, após o F1, o F2, o F3, o F4 e o F5)

Fonte: [divisão de tarefas v2](2026-11-08-divisao-de-tarefas.md). "Depende de" = o que precisa existir antes.

## Andamento

| Item | Situação |
|---|---|
| F2 · Várias fazendas e migrations | **Concluído** (branch `feat/f2-fazendas-migrations`) |
| F1 · Cadastro pelo painel | **Concluído** (branch `feat/f1-cadastro`, que também reconcilia F2, F4 e F5 com o `main`) |
| F3 · Login e perfis | **Concluído** (branch `feat/f3-login`, sobre a `feat/f1-cadastro`); permissões confirmadas pelo time |
| F6 · Conclusão da OS pelo operador | A fazer (há uma tela mínima do G7 para substituir) |
| F5 · Indicadores da fazenda | **Concluído** (branch `feat/f5-indicadores`); falta só a revisão visual do E1 e a tela de cadastro do planejamento de safra (F1) |
| F4 · WhatsApp | **Código concluído** (branch `feat/f4-whatsapp`, guia em [docs/whatsapp.md](../whatsapp.md)); falta a conta, o número, a verificação da empresa e os 2 modelos aprovados na Meta, e uma rodada real |

## 1. Tarefas de outras pessoas que dependem de mim

| Quem espera | O que precisa do Felipe | Item | Situação |
|---|---|---|---|
| Todos | `fazenda_id` e gerentes por fazenda (contrato 20) | F2 | **Liberado.** Leituras do MCP, `agents`, painel e canal já usam |
| Edison (E1) | O seletor de fazenda do design novo troca o provisório do menu: cookie `fazenda`, rota `/fazenda/{id}?volta=` e a função `fazendas()` nos templates | F2 | **Liberado** |
| Edison (E2) | A "Fazenda Demo" (`demo = true`, id 2) para o replay não brigar com incidente real. Falta ele decidir o que o replay precisa nela (hoje: 1 talhão, 1 máquina, 1 operador, Abacus HC, nenhuma OS) | F2 | **Liberado**; ajuste do seed é com ele |
| Edison (E4) | `erp.talhoes.geom` (contrato 18) e `lat`/`lon` como centro | F1 | **Liberado** (006_cadastro.sql) |
| Edison (E3, E6) | Monitor segue lendo `lat` e `lon` como centro do talhão, mesmo com polígono | F1 | **Liberado** (e agora só vigia talhão ativo) |
| Todos | Usuário e papel (`gerente`/`operador`); painel manda o usuário em `respondido_por`; `agents` exige `AGENTS_TOKEN` (contrato 23) | F3 | **Liberado.** `respondido_por` já é o nome do usuário; `AGENTS_TOKEN` é opcional até o time ligar (defina o mesmo valor no `.env`) |
| Gustavo (G7) | O operador marcar a OS como executada chama a geração do relatório; hoje só existe a tela mínima dele | F6 | Parcial: tela mínima do G7 funciona, falta a tela do operador no celular |
| Gustavo (G3) | Formulário de OS do F1 chama `POST /janelas/avaliar` | F1 | **Feito**: `/cadastros/os` usa a mesma conferência da janela |
| Gustavo (G1) | Período da safra por fazenda: hoje `SAFRA_INICIO` no `.env`. Falta uma coluna em `erp.fazendas` (ex.: `safra_inicio`). Posso acrescentar numa migration `004` se ele quiser | F2 | **A combinar** |
| Edison (E5) | Cadastro de insumo (F1) chama a busca de produto dele | F1 | **Feito**: o painel faz proxy da busca e do envio de bula; falta confirmar as credenciais `AGROFIT_*` num ambiente real |

## 2. Tarefas minhas que dependem de dados ou entregas de outros

| Item meu | Depende de | Dono | Situação |
|---|---|---|---|
| F3, F6 (as telas que faltam) | Esqueleto do frontend: menu lateral, componentes base, versão celular (E1) | Edison | **Pronto** (no `main`; F1 e F5 já usam) |
| F1 (cadastro de insumo) | Busca de produto que devolve `produto` e `bula_id` (contrato 1) | Edison (E5) | **Pronto** (usada pelo F1) |
| F1 (OS: aviso e sugestões ao escolher a data) | `POST /janelas/avaliar` (contrato 24) | Gustavo (G3) | **Pronto** |
| F1 (talhão em polígono) | Nada externo | Felipe | **Pronto** |
| F1 ("Monitor vigia o talhão novo no ciclo seguinte") | Monitor ler `erp.talhoes` a cada ciclo | Edison | **Pronto** (testado) |
| F3 (login) | Permissões da tabela: confirmar com o time | Time | **Confirmadas** |
| F3 (`respondido_por` e `AGENTS_TOKEN`) | `agents` passar a exigir o segredo em `POST /incidentes/{id}/resposta` | Gustavo | **Feito por mim** (`agents/api.py` e `config.py`), opcional até o time definir o valor; **avisar o Gustavo** |
| F3 (WhatsApp/Telegram: só gerente aprova) | Contato no canal por usuário | Felipe | **Pronto** (cadastrado em `/usuarios`, sincronizado em `erp.gerentes`) |
| F4 (WhatsApp) | Conta, número e verificação da empresa na Meta; aprovação dos modelos `terramind_aprovacao` e `terramind_aviso` (leva dias, não depende de código) | Externo (Meta) | **Pendente: começar logo** (passo a passo em docs/whatsapp.md) |
| F4 (webhook de resposta) | Rota `/whatsapp/webhook` no `agents` e a mesma `ao_responder` do Telegram | Gustavo | **Feito** (mudei `agents/api.py`, `config.py` e `nodes/executor.py`; avisar o Gustavo). Falta uma URL HTTPS pública do `agents` |
| F4 (custo por mensagem) | Valor no BMC ("a levantar") | Time/negócio | Pendente |
| F5 (indicadores) | Nada externo para estoque, OS e talhões. Tabela de safra criada (`005_safra.sql`) | Felipe | **Pronto**. Redesenho visual depende do E1 |
| F1 (cadastro do planejamento de safra) | Aba "Safra" em `/cadastros` | Felipe | **Pronto** |
| F5 (insumo cujo estoque não cobre as OS agendadas) | Dose e área das OS (já no banco) | Felipe | **Pronto** |
| F6 (conclusão pelo operador) | `erp.execucoes_os` (contrato 28) e `POST /relatorios/os/{id}` (G7) | Gustavo | **Pronto** (migration 020 e relatório existem) |
| F6 (tela para celular) | Componentes e layout de celular do E1 | Edison | Pendente (mesmo bloqueio do topo) |
| F2 (restrição por organização do usuário) | Login do F3 para saber as fazendas de cada usuário | Felipe | **Pronto** |
| F2 (RLS no Postgres, opcional) | F3 (usuário e organização na sessão) | Felipe | Adiado |

## Dúvidas abertas para o time

1. O que falta do meu lado: **F6** (conclusão da OS pelo operador), mais a parte externa do F4 (conta na Meta). Falta o time **definir o valor do `AGENTS_TOKEN`** e ligá-lo no `.env` (o código já aceita).
2. (Resolvida) Tabela de permissões do F3: confirmada pelo time.
3. Gustavo quer o `safra_inicio` por fazenda no G1?
4. Os 2 testes do Monitor (`test_ciclo_manual_usa_fonte_live` e `test_modo_live_roda_ciclo_em_background`) já falhavam no `main` antes do F2: é com o Edison.
