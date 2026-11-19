# Relatório de aplicação (G7) e a decisão D9

Este documento registra o que a pesquisa de 16/11/2026 achou sobre qual relatório vale para cada tipo de aplicação, o que o TerraMind gera hoje e o que ainda depende de um agrônomo.

## O que a norma pede

| Modalidade | Norma | O que exige |
|---|---|---|
| Drone (ARP) | Portaria MAPA 298/2021, art. 10 | Registro de cada aplicação, guardado por 2 anos (art. 19, III), mais relatório mensal no SIPEAGRO até o dia 15 do mês seguinte (art. 11) |
| Avião agrícola | IN MAPA 2/2008, art. 9º | Relatório operacional de cada aplicação no modelo do Anexo I, assinado pelo engenheiro agrônomo responsável técnico em até 10 dias e guardado por 2 anos, mais relatório mensal (art. 14) |
| Terrestre | Não achamos modelo federal | A venda exige receituário agronômico (Lei 14.785/2023). Não confirmamos obrigação federal de relatório operacional por aplicação terrestre. Falta conferir a regra estadual (em Goiás, a Agrodefesa) |

Nenhuma das duas normas fixa limite numérico de vento, temperatura ou umidade. A Portaria 298 manda avaliar as condições meteorológicas durante a operação (art. 9º, IX), e a IN 2/2008 manda interromper o trabalho quando os parâmetros chegam ao limite de segurança (art. 9º, § 7º). O limite numérico vem da bula e do receituário. Por isso o relatório do TerraMind confere o clima contra a bula e cita a página.

### Campos do registro de drone (Portaria 298, art. 10)

Data e hora de início e de término, coordenadas geográficas da área, cultura, área tratada em hectares, tipo de atividade, marca comercial com volume e dosagem, altura do voo, dados meteorológicos (temperatura, umidade relativa, direção e velocidade do vento durante a aplicação), identificação da aeronave e tipo ou modelo da ponta de pulverização. O mapa de aplicação e o receituário, quando houver, ficam anexos.

### Campos do relatório de avião (IN 2/2008, art. 9º)

Além dos campos acima: empresa operadora e registro no MAPA, contratante, localização da propriedade, classe toxicológica e formulação do produto, adjuvante, largura da faixa, modelo e ângulo do equipamento, croqui da área, direção das faixas e do vento, clima no início e no fim da aplicação, coordenadas da pista, prefixo da aeronave e uso de DGPS.

## O que o TerraMind gera hoje

Ao concluir a OS em `/relatorios`, o painel grava a execução (`erp.execucoes_os`, contrato 28) e o `agents` gera o PDF em `POST /relatorios/os/{os_id}`. O PDF traz:

- área tratada: talhão, coordenadas, cultura, estágio e hectares aplicados;
- produto: marca, classe, dose aplicada, dose planejada, volume de calda e número do receituário;
- execução: início e término reais, máquina ou aeronave, operador, responsável técnico e registro profissional; para drone e avião, altura do voo e largura da faixa;
- clima durante a aplicação: temperatura, umidade, vento com direção, rajada, Delta T, chuva e as leituras do início e do fim;
- conferência com os limites: cada limite da bula com a página e o resultado. Limite genérico aparece como referência e não entra no veredito;
- SHA-256 dos dados no rodapé. O mesmo valor fica em `agente.relatorios_aplicacao`, então dá para provar que o PDF não foi editado.

Gerar de novo cria outra versão e mantém a anterior.

## O que falta

1. **Validar os campos com um agrônomo.** O time não tem um, e esta é a condição da D9.
2. **Clima observado.** O clima do relatório vem do modelo do Open-Meteo, e o PDF diz isso. Para valer como registro meteorológico da Portaria 298, o dado tem que ser medido no local (estação da fazenda, termo-higro-anemômetro do operador ou INMET, entrega do E3). Enquanto isso, o operador pode anotar a medição em Observações.
3. **Campos do avião que ainda não existem:** contratante, registro da empresa no MAPA, classe toxicológica, adjuvante, croqui e coordenadas da pista. Dependem do cadastro de prestador e de contratante.
4. **Relatório mensal** (art. 11 da Portaria 298 e art. 14 da IN 2/2008). Os dados já estão no banco; falta a tela que consolida por mês e por município.
5. **Regra estadual para aplicação terrestre.**

## Fontes

- [Portaria MAPA 298/2021 (LegisWeb)](https://www.legisweb.com.br/legislacao/?id=420676) e [texto no gov.br](https://www.gov.br/agricultura/pt-br/assuntos/insumos-agropecuarios/aviacao-agricola/legislacao/portaria-mapa-298-de-22-09-2021.pdf)
- [IN MAPA 2/2008 (LegisWeb)](https://www.legisweb.com.br/legislacao/?id=77607) e [versão consolidada com a IN 37/2020](https://www.gov.br/agricultura/pt-br/assuntos/insumos-agropecuarios/aviacao-agricola/legislacao/in-2-de-2008-com-alteracoes-in-37-de-2020.pdf)
- [Lei 14.785/2023](https://www2.camara.leg.br/legin/fed/lei/2023/lei-14785-27-dezembro-2023-795170-normaatualizada-pl.html)

As duas normas do MAPA foram lidas em cópia de terceiros (LegisWeb), não no Diário Oficial. Antes de vender o relatório como "conforme a norma", confira o texto oficial.
