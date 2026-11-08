# TerraMind · Divisão de tarefas v2

### Contratos novos

| # | Contrato | Dono | Usam |
|---|---|---|---|
| 18 | Geometria do talhão: `erp.talhoes.geom` em GeoJSON (Point ou Polygon, WGS84). `lat` e `lon` continuam e passam a ser o centro, porque o Monitor usa essas colunas | Felipe | Edison |
| 19 | Previsão por talhão: `GET {MONITOR_URL}/previsao/{talhao_id}` (horária, 120 h, formato do `HoraPrevisao`) e `GET {MONITOR_URL}/previsao/{talhao_id}/diaria?dias=16` | Edison | Gustavo, painel |
| 20 | Fazenda: `fazenda_id` em talhões, máquinas, operadores, insumos e OS. Cada fazenda tem seus gerentes; o canal manda cada mensagem ao gerente da fazenda do incidente, não a um `GERENTE_CHAT_ID` fixo | Felipe | todos |
| 21 | Campos novos da previsão, todos opcionais: `vento_dir_graus`, `rajada_kmh`, `delta_t_c`, `inversao`. `Gatilho.tipo` ganha os tipos que saírem do E7. O `agents` funciona sem eles | Edison | Gustavo |
| 22 | Risco por talhão: `GET {MONITOR_URL}/risco?fazenda_id=` → `[{talhao_id, nivel: verde\|amarelo\|vermelho, motivos, ate}]` | Edison | painel |
| 23 | Usuário e papel: `papel` ∈ {`gerente`, `operador`}. O painel manda o usuário logado em `respondido_por`; o `agents` só aceita `POST /incidentes/{id}/resposta` com o segredo `AGENTS_TOKEN` | Felipe | todos |
| 24 | Janelas: `GET {AGENTS_URL}/agenda?fazenda_id=&inicio=` e `POST {AGENTS_URL}/janelas/avaliar` com `{talhao_id, insumo_id, inicio, duracao_horas}` → `{serve, motivo, citacao, sugestoes: [{inicio, maquina_id}]}` | Gustavo | Felipe (formulário de OS), painel |
| 25 | Impactos: `agente.impactos` com incidente, OS, talhão, produto, valor em R$, fator, fonte e situação | Gustavo | Edison (histórico), painel |
| 26 | Limites por produto (se a D6 passar): `conhecimento.limites_produto` com produto, parâmetro, limite, unidade, janela em horas, trecho, documento e página | Gustavo | Edison (bulas novas) |
| 27 | Origem do incidente: `agente.incidentes.origem` ∈ {`live`, `replay`}. Incidente de replay não entra no histórico nem no dashboard de perda | Edison | Gustavo |
| 28 | Execução da OS: `erp.execucoes_os` com início e fim reais, área, dose, volume de calda, máquina, operador, receituário, responsável técnico e observações. Ao marcar a OS como executada, o painel chama a geração do relatório (G7) | Felipe | Gustavo |
| 29 | Faixas de migration: Felipe `003`–`009` e seed `9xx`, Edison `011`–`019`, Gustavo `020`–`029` | Felipe | todos |

## Felipe · fazenda, acesso e canais (21 pontos)

### F1 · Cadastro pelo painel (5)

- Telas para criar, editar e desativar talhões, máquinas, operadores e OS. Hoje tudo entra pelo `900_seed.sql`.
- Talhão: ponto ou polígono desenhado no mapa (Leaflet com plugin de desenho). O desenho vai para `erp.talhoes.geom` (contrato 18); `lat` e `lon` recebem o centro; a área do polígono sugere o `area_ha`.
- OS: só aceita máquina do tipo certo e livre no horário (mesma regra de `disponibilidade_maquinas` em `erp_mcp/leitura.py`) e insumo com estoque. Ao escolher a data, mostra o aviso e as sugestões do G3.
- Toda escrita grava em `agente.auditoria` com o nó `painel`, como as escritas do MCP.
- O mapa vira um componente reutilizável: o E4 usa o mesmo.

**Pronto quando:** um gerente cadastra do zero 2 talhões (um ponto e um polígono), 1 máquina, 1 operador e 1 OS sem SQL, e o Monitor vigia o talhão novo no ciclo seguinte.

### F2 · Várias fazendas e migrations incrementais (4)

- Migration com `erp.organizacoes` e `erp.fazendas`, e `fazenda_id` nas tabelas do ERP (contrato 20, modelo da D7). Seed com uma fazenda de exemplo e uma "Fazenda Demo" só para o replay (ver E2).
- Filtro por fazenda nas leituras do painel (`painel/dados.py`) e do MCP. `disponibilidade_maquinas` e `listar_operadores` filtram pela fazenda do talhão; senão o plano pode alocar máquina de outra fazenda. O Monitor continua vigiando todos os talhões.
- Isolamento por organização na aplicação; Row Level Security do Postgres como segunda camada, se der tempo.
- Gerente por fazenda: hoje o `GERENTE_CHAT_ID` do `.env` é um só. O `TelegramChannel` e o futuro `WhatsAppChannel` passam a buscar o destino pela fazenda do incidente.
- Runner de migrations: hoje os SQL só rodam quando o banco é criado, e qualquer mudança exige `docker compose down -v`, que apaga tudo o que foi cadastrado. O runner aplica só as migrations novas e registra as aplicadas numa tabela de controle.

**Pronto quando:** duas fazendas de organizações diferentes rodam juntas, cada incidente chega só ao gerente da fazenda certa, e uma migration nova sobe sem apagar os dados.

### F3 · Login e perfis (4)

- Usuários com senha em hash (argon2 ou bcrypt), organização, fazendas, papel e contato no canal (chat do Telegram ou telefone do WhatsApp). O gerente cadastra os usuários da fazenda.
- Login por sessão no painel; toda rota pede usuário logado.
- Permissões (proposta, confirmar com o time):

  | Ação | Gerente | Operador |
  |---|---|---|
  | Ver OS, mapa, agenda, indicadores e histórico | sim | sim |
  | Cadastrar talhões, máquinas, insumos e usuários | sim | não |
  | Criar e editar OS | sim | sim |
  | Aprovar ou recusar plano de reagendamento | sim | não |
  | Registrar a execução da OS (F6) | sim | sim |

- O nome de quem aprova pelo painel vai para `respondido_por`, e o `agents` passa a exigir o segredo compartilhado (contrato 23). No Telegram e no WhatsApp, só o chat de um gerente aprova.

**Pronto quando:** o operador logado não vê os botões de aprovação e recebe 403 se chamar a rota direto, e a auditoria mostra o nome de quem aprovou.

### F4 · WhatsApp como canal (4)

- `channels/whatsapp.py` com `WhatsAppChannel(NotificationChannel)` e a entrada `whatsapp` em `_CANAIS` (`channels/base.py`). Os nós do `agents` não mudam.
- WhatsApp Cloud API (Meta). Três diferenças em relação ao Telegram:
  - A resposta chega por webhook HTTPS público, não por polling. Precisa de rota no `agents` e conferência da assinatura da Meta.
  - Fora da janela de 24 h desde a última mensagem do usuário, só sai mensagem de modelo (template) aprovado pela Meta. O plano de reagendamento precisa de um modelo com 3 botões de resposta rápida; os rótulos atuais cabem no limite de 20 caracteres.
  - Cada mensagem de modelo é cobrada (no BMC, custo "a levantar").
- Abrir a conta, o número e a verificação da empresa na Meta na primeira semana: leva dias e não depende de código.

**Pronto quando:** o incidente da demo chega no WhatsApp com os 3 botões, o clique retoma a sessão, e o segundo clique responde "decisão já registrada" (os mesmos testes do Telegram).

### F5 · Indicadores da fazenda (2)

- Bloco no painel com os insumos e o estoque de cada um, número de talhões e área total, OS por status (agendadas, suspensas, reagendadas, executadas), e o que está plantado e o que vai ser plantado em cada talhão.
- "O que vai ser plantado" não existe no schema: criar o planejamento de safra (talhão, safra, cultura, plantio previsto).
- Outras variáveis sugeridas: máquinas em manutenção, OS dos próximos 7 dias e insumo cujo estoque não cobre as OS agendadas.

**Pronto quando:** os números do bloco batem com o banco para a fazenda selecionada.

### F6 · Conclusão da OS pelo operador (2)

- Hoje nenhuma tela marca a OS como `executada`: o status existe no schema, mas nada o usa.
- O operador marca a OS como executada e registra os dados reais do contrato 28. Tela pensada para o celular.
- Ao concluir, o painel chama a geração do relatório (G7).

**Pronto quando:** a OS concluída aparece como Executada, com os dados reais gravados e registrados na auditoria.

## Edison · clima, bulas e interface (21 pontos)

### E1 · Design do frontend: fundação (3)

- O painel hoje é uma tela única de acompanhamento. Com esta lista ele passa a ter umas 10 telas: login, visão geral, cadastros, fazenda, mapa, agenda, perda evitada, histórico, Pergunte à bula e relatórios.
- Montar o esqueleto que todos vão usar: menu lateral, seletor de fazenda, usuário logado no topo, cores e fontes do pitch, componentes base (cartão de indicador, tabela, formulário, selo de status, faixa de alerta, estado vazio) e versão de celular para o operador.
- Entregar primeiro, num PR pequeno, antes de os outros criarem telas. A tela única atual vira a "Visão geral".
- Na fase 3, uma revisão visual de todas as telas.

**Pronto quando:** existe uma página com todos os componentes e um guia curto de como criar uma tela nova.

### E2 · Monitor ao vivo por padrão (2)

- O laço ao vivo sempre roda (desligável só nos testes). `MONITOR_MODE=replay` sai do `.env.example`, do `docker-compose.yml` e do padrão em `monitor/main.py`. O `POST /replay` continua para o botão Simular chuva.
- O replay roda na "Fazenda Demo" do seed (F2), para não brigar com incidentes reais: só pode haver um incidente ativo por talhão (contrato 3). Incidente de replay leva `origem = 'replay'` (contrato 27).
- Guardar cada previsão buscada (`clima.previsoes`) e expor a previsão por talhão (contrato 19). O gráfico do painel passa a mostrar a previsão real do talhão, em vez da cópia `painel/static/chuva_80mm.json`.
- Cota do Open-Meteo: o BMC já conta com o plano comercial (US$ 29/mês). Talhões vizinhos caem no mesmo ponto da grade e podem dividir uma consulta.
- Fazer este item primeiro: o histórico (E6) precisa de dias de previsões reais guardadas.

**Pronto quando:** `docker compose up` sem mexer no `.env` mostra a previsão real de cada talhão, e Simular chuva continua funcionando.

### E3 · Novas variáveis de clima (4)

- Delta T (temperatura do ar menos a de bulbo úmido), calculado da temperatura e da umidade (fórmula de Stull). Faixa boa: 2 a 8.
- Inversão térmica: indicador por hora que compara a temperatura em 2 m com a de 80 ou 120 m (o Open-Meteo tem esses níveis para alguns modelos; conferir para Goiás) e considera vento fraco e madrugada de céu limpo.
- Direção e rajada do vento (`wind_direction_10m`, `wind_gusts_10m`).
- Clima observado: estação automática do INMET mais próxima do talhão (conferir o acesso; a API pode pedir token) ou a estação da própria fazenda, quando houver.
- Previsão dos próximos dias: resumo diário de até 16 dias.
- Campos novos opcionais no `HoraPrevisao` e no `previsao_snapshot` (contrato 21). Gatilhos novos em `monitor/avaliacao.py`, com os limites do E7.
- Cartões no painel.

**Pronto quando:** o painel mostra, para cada talhão, Delta T, risco de inversão, vento com direção, a última leitura observada e os próximos 7 dias.

### E4 · Mapa de risco dos talhões (3)

- Risco das próximas 48 h por talhão (verde, amarelo ou vermelho), calculado no Monitor com os limiares dos gatilhos mais Delta T e inversão (contrato 22).
- Mapa no painel com cada talhão colorido. O clique abre as OS do talhão nas 48 h e o motivo do risco. Reusa o componente de mapa do F1.

**Pronto quando:** com o replay de 80 mm, o talhão da demo fica vermelho e o clique mostra a OS de Abacus HC.

### E5 · AgroFit: novas bulas no RAG (4)

- Começar por um spike de 1 dia na API Agrofit da plataforma AgroAPI da Embrapa (pede cadastro e token). Responder: que dados vêm (registro no MAPA, marca, titular, classe, culturas, alvos), se vem o link do PDF da bula e qual o limite de uso do plano gratuito.
- Se não vier o PDF: AgroFit para os dados do produto, e a bula baixada da ADAPAR (de onde vieram as 4 atuais) ou do site do fabricante.
- Trocar o `fontes.yaml` por registro no banco (`conhecimento.documentos` já existe) e indexação incremental: bula nova ou atualizada é indexada sem duplicar no Qdrant.
- Busca de produto para o cadastro de insumo (F1): o `produto` e o `bula_id` vêm daqui, o que mantém o contrato 1.
- Conferir nas bulas novas se a busca por `produto` acha o limite de chuva (contrato 10: cada bula põe o limite numa seção diferente).

**Pronto quando:** cadastrar um produto que ainda não está na base baixa e indexa a bula, e o Avaliador cita a página dela num incidente.

### E6 · Histórico: previsto × observado (3)

- Para cada incidente ao vivo: a chuva prevista na janela da OS (do `previsao_snapshot`) contra a chuva que caiu. Fonte: a estação do E3; como plano B, a reanálise do Open-Meteo, com aviso na tela de que não é estação.
- Classificar cada suspensão: correta (choveu acima do limite citado), falso alarme ou sem dado.
- Tela com a porcentagem de suspensões corretas, o erro médio da previsão e a lista de incidentes. Ignora incidentes de replay.
- Uso principal: números reais para o pitch e para calibrar os limiares.

**Pronto quando:** depois de alguns dias de monitor ao vivo, a tela mostra quantas suspensões estavam certas, com os dados de cada uma.

### E7 · Pesquisa: o que afeta a aplicação além da chuva (2)

- Levantar nas bulas e em referências técnicas (Embrapa, tecnologia de aplicação) os limites de vento (mínimo e máximo), Delta T, inversão térmica, temperatura, umidade, orvalho, chuva antes da aplicação, solo encharcado (trânsito de máquina), horário, estádio fenológico, intervalo de reentrada e carência.
- Entregar uma tabela no repo com a condição, o limite, a fonte e a página, se dá para medir com previsão ou estação, e o nome do parâmetro no sistema.
- Fatores de perda para vento, umidade e temperatura em `fatores_perda.yaml` (hoje só existe chuva), com fonte ou estimativa declarada.
- Fator de perda por dia de atraso para o G5, com fonte ou estimativa declarada.

**Pronto quando:** a tabela está no repo, e o E3 e o G6 usam os parâmetros dela.

## Gustavo · decisão e IA (21 pontos)

### G1 · Dashboard de perda evitada (3)

- Hoje a perda só existe no JSON da auditoria, e o total do painel é somado no navegador (`painel/static/painel.js`). Gravar em `agente.impactos` (contrato 25): o `acao_preventiva` escreve, e o `executor` e o `encerrar` atualizam.
- Período da safra configurável por fazenda.
- Tela com o total na safra, por talhão e por produto; os planos aprovados, recusados e em decisão manual; e o tempo médio até a resposta do gerente. É o relatório de fim de safra do BMC, base da renovação.

**Pronto quando:** os números da tela batem com a auditoria dos incidentes da demo, e os de replay ficam de fora.

### G2 · Agenda semanal inteligente (4)

- `janelas_climaticas` (`agents/rules/janelas.py`) depende do limite que o Avaliador citou num incidente. A agenda roda antes de qualquer incidente, então precisa conhecer antes o limite de cada produto (D6). Se a D6 passar, extrair uma vez por bula os limites com trecho e página e guardar em `conhecimento.limites_produto` (contrato 26). Se não passar, a agenda usa os limiares genéricos do Monitor.
- Generalizar as janelas para receber os limites do produto e os parâmetros novos (Delta T, inversão).
- `GET /agenda` no `agents` (contrato 24): por talhão e por dia, as faixas boas e ruins, com o motivo.
- Tela com a semana por talhão, sobre a previsão real (E2).

**Pronto quando:** o gerente abre a semana e vê, por talhão, as horas boas e ruins para o produto escolhido, com o motivo e a página da bula.

### G3 · Sugestão de janela na criação da OS (3)

- `POST /janelas/avaliar` (contrato 24) recebe talhão, insumo, início e duração. Devolve se a janela serve, o motivo quando não serve ("terça 14h: 12 mm de chuva nas 4 h depois da aplicação; a bula pede 4 h sem chuva, p. 7") e as 3 próximas janelas boas com máquina livre (reusa `agents/rules/validador.py`).
- O formulário de OS do F1 chama a rota quando o gerente escolhe a data.

**Pronto quando:** ao agendar Abacus HC num horário de chuva, o formulário avisa, sugere outro horário com máquina livre e preenche o campo quando o gerente aceita.

### G4 · Pergunte à bula (3)

- Agente novo no ADK: recebe a pergunta ("posso aplicar Abacus HC com vento de 12 km/h?"), busca com `buscar_trechos` e responde com trecho, fonte e página.
- Mesma garantia do Avaliador: o schema exige a citação, e o código confere que a página citada veio da busca. Sem trecho, a resposta é "a bula não traz isso".
- Tela de chat no painel. Se der tempo, pergunta livre pelo Telegram ou WhatsApp, junto com o Felipe.
- 20 perguntas de teste com a resposta esperada.

**Pronto quando:** as 20 perguntas passam e toda resposta mostra a página.

### G5 · Perda por atraso na decisão (3)

- A OS em risco já é suspensa sozinha; o que custa é a demora do gerente. Enquanto o plano espera o SIM, as janelas boas passam e a aplicação fica para mais tarde.
- Cálculo em código, como a perda evitada: valor da aplicação × fator de atraso por dia (E7), mais o horário em que vence a próxima janela boa.
- Mostrar no painel e no lembrete de `agents/timeout.py`: "sem decisão há 3 h; esperar até a próxima janela custa R$ X; a janela de quarta 06h vence em 5 h".
- Se a OS não foi suspensa (o Avaliador falhou e o caso foi para decisão manual), o custo é a perda inteira de aplicar na chuva.

**Pronto quando:** o lembrete traz o valor e o horário em que a próxima janela vence, e o valor sai de código.

### G6 · Além da chuva nos agentes e nas janelas (3)

- Com a tabela do E7: parâmetros novos em `Parametro` e `FAIXAS` (`agents/modelos.py`), por exemplo `delta_t`, `inversao` e `rajada`.
- `previsao_da_os` (`agents/rules/previsao.py`) agrega os campos novos do E3.
- O prompt e o schema do Avaliador, o `janelas.py` e o Validador tratam os parâmetros novos. Testes em `agents/tests/test_rules.py` para cada um.

**Pronto quando:** um cenário de replay com vento forte (ou Delta T alto) e sem chuva suspende a OS citando a página certa e propõe uma janela que respeita o limite.

### G7 · Relatório de aplicação da MAPA (2)

- Confirmar antes qual relatório vale (D9). A aviação agrícola (IN MAPA 2/2008) e os drones (Portaria MAPA 298/2021) exigem relatório operacional de cada aplicação. Para aplicação terrestre, verificar se há exigência federal ou estadual e qual o modelo. Validar os campos com um agrônomo.
- Gerar o PDF ao concluir a OS (F6), só com dados do banco: fazenda, talhão, cultura, produto, dose, área, máquina, operador, início e fim reais, clima durante a aplicação (temperatura, umidade, vento e direção, do E3), receituário e responsável técnico.
- Guardar e listar os relatórios na tela de OS.

**Pronto quando:** concluir a OS da demo gera o PDF com todos os campos preenchidos.
