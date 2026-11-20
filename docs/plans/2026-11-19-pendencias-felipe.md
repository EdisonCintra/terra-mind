# Pendências do Felipe (atualizado em 19/11/2026, após o F2)

Fonte: [divisão de tarefas v2](2026-11-08-divisao-de-tarefas.md). "Depende de" = o que precisa existir antes.

## Andamento

| Item | Situação |
|---|---|
| F2 · Várias fazendas e migrations | **Concluído** (branch `feat/f2-fazendas-migrations`) |
| F1 · Cadastro pelo painel | A fazer |
| F3 · Login e perfis | A fazer |
| F6 · Conclusão da OS pelo operador | A fazer (há uma tela mínima do G7 para substituir) |
| F5 · Indicadores da fazenda | A fazer |
| F4 · WhatsApp | A fazer (começa pela conta na Meta) |

## 1. Tarefas de outras pessoas que dependem de mim

| Quem espera | O que precisa do Felipe | Item | Situação |
|---|---|---|---|
| Todos | `fazenda_id` e gerentes por fazenda (contrato 20) | F2 | **Liberado.** Leituras do MCP, `agents`, painel e canal já usam |
| Edison (E1) | O seletor de fazenda do design novo troca o provisório do menu: cookie `fazenda`, rota `/fazenda/{id}?volta=` e a função `fazendas()` nos templates | F2 | **Liberado** |
| Edison (E2) | A "Fazenda Demo" (`demo = true`, id 2) para o replay não brigar com incidente real. Falta ele decidir o que o replay precisa nela (hoje: 1 talhão, 1 máquina, 1 operador, Abacus HC, nenhuma OS) | F2 | **Liberado**; ajuste do seed é com ele |
| Edison (E4) | Componente de mapa reutilizável (Leaflet com desenho) e `erp.talhoes.geom` (contrato 18) | F1 | Bloqueado até o F1 |
| Edison (E3, E6) | Monitor segue lendo `lat` e `lon` como centro do talhão, mesmo com polígono | F1 | Bloqueado até o F1 |
| Todos | Usuário e papel (`gerente`/`operador`); painel manda o usuário em `respondido_por`; `agents` exige `AGENTS_TOKEN` (contrato 23) | F3 | Bloqueado até o F3 |
| Gustavo (G7) | O operador marcar a OS como executada chama a geração do relatório; hoje só existe a tela mínima dele | F6 | Parcial: tela mínima do G7 funciona, falta a tela do operador no celular |
| Gustavo (G3) | Formulário de OS do F1 chama `POST /janelas/avaliar` | F1 | O G3 já tem formulário mínimo; o F1 o substitui |
| Gustavo (G1) | Período da safra por fazenda: hoje `SAFRA_INICIO` no `.env`. Falta uma coluna em `erp.fazendas` (ex.: `safra_inicio`). Posso acrescentar numa migration `004` se ele quiser | F2 | **A combinar** |
| Edison (E5) | Cadastro de insumo (F1) chama a busca de produto dele | F1 | Ver seção 2 (a dependência é minha) |

## 2. Tarefas minhas que dependem de dados ou entregas de outros

| Item meu | Depende de | Dono | Situação |
|---|---|---|---|
| F1, F3, F5, F6 (todas as telas) | Esqueleto do frontend: menu lateral, componentes base, versão celular (E1), entregue primeiro num PR pequeno | Edison | **Pendente.** Principal bloqueio das telas; sem ele cada um cria um visual |
| F1 (cadastro de insumo) | Busca de produto que devolve `produto` e `bula_id` (contrato 1), base de bulas no banco | Edison (E5) | Pendente. Dá para cadastrar com a lista atual de produtos até o E5 sair |
| F1 (OS: aviso e sugestões ao escolher a data) | `POST /janelas/avaliar` (contrato 24) | Gustavo (G3) | **Pronto** |
| F1 (talhão em polígono) | Nada externo; o E4 é quem depende do meu componente | Felipe | Livre |
| F1 ("Monitor vigia o talhão novo no ciclo seguinte") | Monitor ler `erp.talhoes` a cada ciclo | Edison | Pronto: ele já lê todos os talhões do banco |
| F3 (login) | Permissões da tabela (proposta): confirmar com o time | Time | **Pendente: decisão** |
| F3 (`respondido_por` e `AGENTS_TOKEN`) | `agents` passar a exigir o segredo em `POST /incidentes/{id}/resposta` | Gustavo | Pendente; combinar a mudança no `agents/api.py` |
| F3 (WhatsApp/Telegram: só gerente aprova) | Contato no canal por usuário | Felipe | Livre (depois do F3 base) |
| F4 (WhatsApp) | Conta, número e verificação da empresa na Meta; aprovação do modelo de mensagem com 3 botões (leva dias, não depende de código) | Externo (Meta) | **Pendente: começar logo** |
| F4 (webhook de resposta) | Rota pública no `agents` e a mesma `ao_responder` do Telegram | Gustavo | Combinar: a rota fica no serviço dele |
| F4 (custo por mensagem) | Valor no BMC ("a levantar") | Time/negócio | Pendente |
| F5 (indicadores) | Nada externo para estoque, OS e talhões. "O que vai ser plantado" exige tabela nova de safra: minha | Felipe | Livre |
| F5 (insumo cujo estoque não cobre as OS agendadas) | Dose e área das OS (já no banco) | Felipe | Livre |
| F6 (conclusão pelo operador) | `erp.execucoes_os` (contrato 28) e `POST /relatorios/os/{id}` (G7) | Gustavo | **Pronto** (migration 020 e relatório existem) |
| F6 (tela para celular) | Componentes e layout de celular do E1 | Edison | Pendente (mesmo bloqueio do topo) |
| F2 (restrição por organização do usuário) | Login do F3 para saber as fazendas de cada usuário | Felipe | Depende do F3 |
| F2 (RLS no Postgres, opcional) | F3 (usuário e organização na sessão) | Felipe | Adiado |

## Dúvidas abertas para o time

1. O Edison entrega o E1 (esqueleto) antes de eu começar o F1? Se não, começo pelo que não tem tela nova: F4 (conta na Meta) e a parte de dados do F5.
2. Tabela de permissões do F3: confirmar (operador cria e edita OS? aprova plano?).
3. Gustavo quer o `safra_inicio` por fazenda no G1?
4. Os 2 testes do Monitor (`test_ciclo_manual_usa_fonte_live` e `test_modo_live_roda_ciclo_em_background`) já falhavam no `main` antes do F2: é com o Edison.
