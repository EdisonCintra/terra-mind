# Spike: clima observado (INMET x reanálise)

Data do spike: 2026-11-17. Alvo: Rio Verde (GO), em torno de -17,79 / -50,92.

## INMET (`apitempo.inmet.gov.br`)

- Tentativa: uma requisição pública simples (`GET https://apitempo.inmet.gov.br/estacoes/T`, lista de estações automáticas), sem token.
- Resultado: a conexão foi encerrada pelo servidor antes de qualquer resposta HTTP (`Connection was reset`, erro de TLS/recv 56). Nenhum status, nenhum corpo.
- Não foi tentado contornar o bloqueio (sem troca de cabeçalhos, proxy ou token de terceiros), conforme a regra do spike.
- Sem resposta, não foi possível confirmar: se pede token, como se solicita, a estação automática mais próxima (código, nome, distância), nem formato e atraso das leituras horárias. Esses pontos ficam em aberto.

Decisão: INMET não serve até aqui. `InmetFonte` não foi implementada. Reabrir se o INMET passar a responder (ou se o dono obtiver acesso/token pelo canal oficial do instituto).

## Reanálise Open-Meteo (caminho adotado)

- `https://archive-api.open-meteo.com/v1/archive`, sem chave, respondeu 200.
- Campos horários: `precipitation`, `temperature_2m`, `relative_humidity_2m`, `wind_speed_10m`, `wind_direction_10m`; `wind_speed_unit=kmh`, `timezone=America/Sao_Paulo`.
- A grade devolve o ponto mais próximo (para o talhão do seed: -17,75 / -50,93, 763 m).
- Atraso: dados de 5 a 7/11 vieram preenchidos numa consulta feita em 17/11. As horas mais recentes (últimos dias) podem vir `null`; o código trata `null` como ausente (`None`), nunca 0, e a rota devolve a última hora com algum dado.
- Fixture real: `monitor/tests/fixtures/open_meteo_archive.json`.

## O que ficou implementado

- Tabelas `clima.estacoes` e `clima.observacoes` (013), `Estacao` e `estacao_mais_proxima` (haversine, raio 50 km), prontas para estações futuras (INMET ou da fazenda).
- `GET /observado/{talhao_id}`: reanálise sob demanda, com `fonte="reanalise"`, `estacao=null`, `distancia_km=null`, cache em memória de 1 h por talhão.
- Coleta horária no laço ao vivo: só roda para estações registradas em `clima.estacoes` e com uma fonte de estação injetada; hoje não há nenhuma, então não faz nada.
