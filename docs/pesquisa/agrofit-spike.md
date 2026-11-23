# Spike: API Agrofit (E5, Task 18)

Feito em 12/11/2026 com a conta do Edison na AgroAPI (Embrapa).

## Acesso

- Catálogo: https://www.agroapi.cnptia.embrapa.br/store/apis/info?name=AGROFIT&version=v1&provider=agroapi (plano gratuito).
- Credenciais: Consumer Key e Consumer Secret da aplicação na loja (menu Applications → Production Keys). No `.env`: `AGROFIT_CLIENT_ID` e `AGROFIT_CLIENT_SECRET`.
- Token: `POST https://api.cnptia.embrapa.br/token` com `grant_type=client_credentials` e autenticação básica (key:secret). Resposta: `access_token`, `token_type: Bearer`, `expires_in` ≈ 3500 s. O código precisa renovar o token quando expira.
- Base: `https://api.cnptia.embrapa.br/agrofit/v1` (a mesma para produção e sandbox).
- Limite de uso: a página não informa, e a resposta não traz cabeçalho de limite. Paginação: `X-Records-Count`, `X-Pages`, `X-Page-Size: 100`.

## Busca de produto

`GET /search/produtos-formulados?marca_comercial=<nome>` (também aceita `q`, `numero_registro`, `cultura`, `titular_registro`, `ingrediente_ativo`, `praga_nome_comum`, `page`). Respostas guardadas em `base_agricola/tests/fixtures/agrofit_abacus_hc.json` e `agrofit_brucia.json`.

| Campo | Exemplo (Abacus HC) | Observação |
|---|---|---|
| `numero_registro` | `"9210"` | texto; vira `registro_mapa` |
| `marca_comercial` | `["Abacus HC"]` | **lista**; o primeiro item vira o `produto` (contrato 1) |
| `titular_registro` | `"Basf S.A."` | |
| `classe_categoria_agronomica` | `["Fungicida"]` | lista; mapear para `erp.insumos.tipo` (contrato 15) em minúsculas |
| `indicacao_uso` | `[{cultura, praga_nome_cientifico, praga_nome_comum[]}]` | 20 itens no Abacus HC; culturas = valores distintos de `cultura` |
| `documento_cadastrado` | `[{descricao, tipo_documento, data_inclusao, url, origem}]` | bula = item com `tipo_documento == "Bula"`; pegar o de `data_inclusao` mais recente |
| `url_agrofit` | página do produto no Agrofit | |

Brucia: registro `19123`, ISK, Herbicida, cultura Milho, bula `Bula_08_Brucia`.

## Bula em PDF

- A API traz o link da bula (`agrofit.agricultura.gov.br/agrofit_cons/agrofit.ap_download_blob_agrofit?...`).
- **O download automático não funciona:** o servidor do MAPA encerra a conexão de clientes HTTP de script. No navegador, o PDF baixa normalmente.
- Não vamos contornar o bloqueio. Decisão: a API dá os dados do produto e o link; o gerente abre o link, baixa o PDF e o envia no cadastro (upload). Ver Task 20 no plano.
- Conferência: a bula rev. 11 (27/09/2024) baixada do Agrofit tem o mesmo texto da cópia da ADAPAR já indexada (17 páginas; limite de chuva na p. 7: "chuvas dentro de um período de quatro (4) horas após a aplicação"). Os arquivos diferem byte a byte (sha256 diferente), então a indexação incremental reindexaria se o arquivo for trocado.

## Pendências

- Confirmar o limite de chamadas do plano gratuito (perguntar a agroapi@embrapa.br, se for preciso).
- Conferir se todo produto da busca tem um item `Bula`; o que não tiver precisa da URL ou do PDF de outra fonte.

## Conferência ponta a ponta (Task 21, 20/11/2026)

Stack completa num projeto isolado (`docker compose -p terramind-e2e`, portas 5434/16333/18000/18010/18020/18030/18080 por um arquivo de override fora do repositório). Carga inicial: `conhecimento-carga` terminou com código 0 e as 4 bulas do repositório ficaram indexadas.

### Envio das bulas novas (`POST /produtos/{registro}/bula`)

| Produto | Registro | HTTP | Resposta |
|---|---|---|---|
| Fox Xpro | 24117 | 201 | `{"bula_id":"bula-fox-xpro","produto":"Fox Xpro","trechos":132}` |
| Engeo Pleno S | 6105 | 201 | `{"bula_id":"bula-engeo-pleno-s","produto":"Engeo Pleno S","trechos":201}` |

`GET /produtos/busca?q=Fox` devolveu `bula_id: "bula-fox-xpro"` só para o Fox Xpro (os outros Fox, `null`). `GET /produtos/busca?q=Engeo` devolveu `bula_id: "bula-engeo-pleno-s"` só para o Engeo Pleno S. A busca no Agrofit funcionou nos dois casos.

### `conferir-chuva`

Saída (código de saída 0, nenhum `SEM TRECHO`):

```
bula-abacus-hc · 7 · produto e aumentando o potencial de deriva. Evitar aplicações em condições de baixa umidade relativa do ar (menores que
bula-amplexus · 5 · - Período de chuvas: A ocorrência de chuvas dentro de um período de quatro (4) horas após aplicação pode afetar o desemp
bula-ativum · 6 · produto e aumentando o potencial de deriva. Evitar aplicações em condições de baixa umidade relativa do ar (menores que
bula-brucia · 9 · Bula_BRUCIA_09 9 • A ocorrência de chuvas ou orvalho intenso até quatro horas após a aplicação do produto poderá diminui
bula-engeo-pleno-s · 28 · embalagem, por 30 segundos; • Toda a água da lavagem é dirigida diretamente para o tanque do pulverizador; • Inutilize a
bula-fox-xpro · 12 · Ao utilizar pulverizadores dotados de equipamento de lavagem sob pressão seguir os seguintes procedimentos: - Encaixe a
```

Achados:

- **Os dois produtos novos não declaram intervalo sem chuva** (confere com a leitura manual das bulas: só temperatura < 30 °C, UR > 55% e vento: Fox Xpro 3 a 10 km/h, p. 5-6; Engeo Pleno S 3 a 18 km/h, ou até 20 km/h, p. 10-11).
- **O comando não acusa isso.** Em vez de `SEM TRECHO`, devolve o "melhor trecho" de cada uma, que não tem relação com chuva: instruções de lavagem de embalagem (Engeo Pleno S, p. 28; Fox Xpro, p. 12). A busca semântica sempre devolve algo; o comando só imprime `SEM TRECHO` quando a busca volta vazia, o que nunca acontece com uma bula indexada. O resultado é um falso negativo da conferência: a bula sem limite de chuva passa como "tem trecho" e o código de saída fica 0. Precisaria de um corte de score ou de uma checagem por palavra-chave ("chuva" no trecho) para ficar fiel ao objetivo.

### OS e replay

- O Engeo Pleno S do seed (`erp.insumos` id 2) continua com `bula_id` NULL depois do envio: o `POST /produtos/{registro}/bula` indexa a bula, mas não liga o insumo do ERP a ela. Para o teste, liguei à mão (`UPDATE erp.insumos SET bula_id = 'bula-engeo-pleno-s'`) e criei o insumo Fox Xpro (`bula-fox-xpro`). Sem esse passo manual o Avaliador nunca veria a bula nova.
- Criei duas OS no Talhão 1 (Fox Xpro daqui a 22 h; Engeo Pleno S daqui a 24 h), junto com a OS 1 do seed (Abacus HC).
- `POST /replay` (`chuva_80mm.json`, talhão 1) devolveu `{"incidentes":[1],"erros":[]}`. O incidente 1 já existia: foi aberto pelo ciclo ao vivo do Monitor na subida, com previsão real (vento 14 km/h, rajada 31 km/h), não pelo cenário de 80 mm. O replay reaproveitou o incidente aberto, então o cenário de chuva não foi de fato exercitado, e o incidente ficou com `origem = 'live'`.
- **Avaliador: não rodou.** A auditoria do incidente 1 tem `listar_os_em_risco` com as OS 1, 4 e 5, e logo depois `avaliacao_falhou`:
  `BadRequestError ... OpenAIException ... "Your API key has expired. Create a new API key to continue." (expired_secret_key, 401)`, com 2 novas tentativas. O fluxo caiu em `pedir_decisao_manual` (`avaliacao_indisponivel`) e o incidente ficou `aguardando_decisao_manual`: "não consegui avaliar o risco das OS 1, 4, 5. Elas continuam AGENDADAS ... a perda é de R$ 5.520,00 (estimativa)."
- Portanto **não há citação da bula nova nem de página**, e não houve limite de chuva inventado: o modelo nem foi chamado. Esta parte do Step 5 está **pendente** até haver uma `OPENAI_API_KEY` válida no `.env`. `conhecimento.limites_produto` ficou vazia (0 linhas).
- Ao refazer: criar as OS e ligar os insumos, derrubar o ciclo ao vivo (ou `MONITOR_MODE=replay`) para o incidente nascer do cenário de 80 mm, e conferir em `agente.auditoria` se o Avaliador cita `bula-fox-xpro` / `bula-engeo-pleno-s` com página, ou se cai em outro limite (vento) sem inventar chuva.

### Falhas e pontos fracos (resumo)

1. `conferir-chuva` devolve trecho irrelevante (lavagem de embalagem) para bulas sem limite de chuva, em vez de `SEM TRECHO`; saída 0.
2. O envio da bula não preenche `erp.insumos.bula_id`.
3. Chave OpenAI do `.env` expirada (401): Avaliador não avaliou; sem citação para conferir.
4. Replay não gerou incidente novo quando já havia um aberto do ciclo ao vivo.
