# Condições de aplicação além da chuva (E7)

Levantamento do que afeta a aplicação de defensivos. Fontes: as bulas indexadas (lidas direto dos PDFs em
`base_agricola/dados/docs/`, páginas contadas a partir de 1) e a Circular Técnica 73 da Embrapa.
Tudo que não tem fonte lida está marcado como **estimativa**, com a justificativa.

Referências:

- **bula-abacus-hc** (fungicida), **bula-ativum** (fungicida), **bula-brucia** (herbicida), **bula-amplexus** (herbicida).
- **Embrapa CT 73**: SOUZA, R. T.; PALLADINI, L. A. *Tecnologia para aplicação de produtos fitossanitários em videira*. Circular Técnica 73, Embrapa Uva e Vinho, 2007.
  https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/541888/1/cir073.pdf (seção "Efeito do ambiente na pulverização" e "Velocidade e direção do vento", pp. 1 a 3 da PDF).
- Faixa de Delta T de 2 a 8 (ideal 4): material técnico de empresa de previsão do tempo (Disagro, "Ventana de aplicación de productos", https://www.disagro.com.gt/wp-content/uploads/2022/03/17._ventana_de_aplicacion_de_productos_co.pdf), citado só no resultado de busca, **não lido por inteiro**. Por isso o Delta T entra como estimativa.

## Tabela

| Condição | Limite | Fonte (doc e página) | Medível por (previsão / estação / não) | Parâmetro no sistema |
|---|---|---|---|---|
| Vento mínimo | 2 km/h (Abacus HC e Brucia); 3 km/h (Amplexus, Embrapa); abaixo disso a calma pode indicar inversão | bula-abacus-hc:p.7; bula-brucia:p.7; bula-amplexus:p.5; Embrapa CT 73 p.3 | previsão e estação | só no Avaliador (agents); o Monitor não tem limiar de vento mínimo (a calma entra pela inversão, `inversao_vento_max_kmh` = 2) |
| Vento máximo | 10 km/h nas bulas; 9 km/h na Embrapa; sistema usa 11 km/h a 2 m (= 15 km/h a 10 m, margem do projeto) | bula-abacus-hc:p.7; bula-brucia:p.7; bula-amplexus:p.5; Embrapa CT 73 p.3 | previsão e estação | `Limiares.vento_kmh` = 11 (a 2 m) |
| Rajada | Bulas: "não aplicar com rajadas", sem número. 15 km/h a 2 m = estimativa (= 20 km/h a 10 m, acima do máximo sustentado) | bula-brucia:p.7; bula-abacus-hc:p.7 (sem valor) | previsão (`wind_gusts_10m`, convertida para 2 m) e estação | `Limiares.rajada_kmh` = 15 (estimativa, a 2 m) |
| Delta T | 2 a 8 °C. Estimativa: faixa da literatura técnica, não lida em fonte primária. Referência indireta: Embrapa CT 73 Tabela 1 mostra Delta T de 2,2 °C (20 °C, UR 80%) e 7,7 °C (30 °C, UR 50%), com tempo de vida da gota caindo de 57 s para 16 s (gota de 100 µm) | Embrapa CT 73 p.2 (Tabela 1) | previsão (calculado de temperatura e umidade) e estação | `Limiares.delta_t_min_c` = 2, `Limiares.delta_t_max_c` = 8 (estimativa) |
| Inversão térmica | Calma (vento próximo de zero), noite com poucas nuvens, do pôr do sol até a manhã. Estimativa: vento <= 2 km/h à noite sinaliza inversão | bula-brucia:p.8; bula-abacus-hc:p.7; bula-amplexus:p.5 | previsão (proxy: vento baixo + noite) e estação; inversão real exige perfil de temperatura | `Limiares.inversao_vento_max_kmh` = 2 (estimativa) |
| Temperatura | Máx. 30 °C; evitar também temperatura muito baixa ou previsão de geada | bula-abacus-hc:p.7; bula-brucia:p.7; bula-amplexus:p.5-6 | previsão e estação | sem campo em `Limiares` ainda (candidato: Task 9 a 11) |
| Umidade | Mín. 50% (Brucia) ou 60% (Abacus HC, Ativum, Amplexus) | bula-brucia:p.7; bula-abacus-hc:p.7; bula-ativum:p.6; bula-amplexus:p.5-6 | previsão e estação | `Limiares.umidade_min_pct` = 50 (inalterado) |
| Orvalho | Não aplicar com orvalho nem logo após chuva; orvalho intenso até 4 h depois reduz a eficiência | bula-abacus-hc:p.7; bula-amplexus:p.5; bula-brucia:p.8-9 | não (sem sensor de molhamento foliar); proxy: umidade perto de 100% e Delta T baixo | nenhum |
| Chuva antes da aplicação | Não aplicar logo após chuva; herbicida Brucia: não aplicar com possibilidade de chuva em até 48 h | bula-abacus-hc:p.7; bula-brucia:p.9 | previsão e estação | `Limiares.janela_h` = 48 |
| Chuva depois (janela) | Chuva até 4 h após a aplicação pode afetar o desempenho (volume sem número nas bulas; projeto usa 20 mm) | bula-abacus-hc:p.7; bula-ativum:p.6; bula-amplexus:p.5; bula-brucia:p.9 | previsão e estação | `Limiares.chuva_mm` = 20 (inalterado) |
| Solo encharcado | Não aplicar em solo saturado, em chuva intensa ou solo sem drenagem rápida (risco de enxurrada) | bula-brucia:p.9 | não (sem sensor de solo); proxy: chuva acumulada recente | nenhum |
| Horário | Evitar as horas mais quentes do dia (sem horário fixo). Embrapa: depósito cai conforme a manhã esquenta (até 17% de diferença entre horários) | bula-abacus-hc:p.10; bula-brucia:p.12; Embrapa CT 73 p.2 | previsão (temperatura e umidade por hora) | derivado de temperatura e umidade; sem campo próprio |
| Estádio fenológico | Brucia: milho entre V3 e V6; Abacus HC/Ativum: girassol, preventivo em R1 (repetir a cada 15 a 20 dias); Amplexus: dose conforme estádio das plantas daninhas (2 a 4 folhas) | bula-brucia:p.5; bula-abacus-hc:p.4; bula-ativum:p.4; bula-amplexus:p.5 | não (informação da cultura, vem da base agrícola) | nenhum (G6) |
| Intervalo de reentrada | 24 h após a aplicação ou secagem completa da calda, o que for mais longo | bula-abacus-hc:p.8; bula-ativum:p.7; bula-brucia:p.8; bula-amplexus:p.8 | não (regra de calendário da OS) | nenhum |
| Carência | Abacus HC: 14 a 60 dias conforme a cultura (milho 45, soja 60); Brucia: milho 60 dias; Amplexus: milho 96 dias | bula-abacus-hc:p.8; bula-brucia:p.8; bula-amplexus:p.7 | não (regra de calendário da OS) | nenhum |

## Como usar no sistema

Cada condição medível vira um gatilho do Monitor, com o limite lido de `Limiares`:

| Gatilho (`tipo`) | Dispara quando | Campo de `Limiares` |
|---|---|---|
| `chuva` | chuva acumulada na janela >= limite | `chuva_mm`, `janela_h` |
| `vento` | vento acima do máximo (abaixo do mínimo não abre incidente: risco amarelo/Avaliador lê o mínimo da bula) | `vento_kmh` |
| `umidade` | umidade abaixo do mínimo | `umidade_min_pct` |
| `rajada` | rajada acima do limite | `rajada_kmh` |
| `delta_t` | Delta T acima de `delta_t_max_c` (abaixo do mínimo → risco amarelo, sem incidente) | `delta_t_max_c` |
| `inversao` | hora com `inversao is True`: 80/120 m mais quente que 2 m com vento < `inversao_vento_max_kmh`; sem dados de altura, heurística noite + céu limpo < 30% + vento fraco | `inversao_vento_max_kmh` |

Vento e rajada vêm do Open-Meteo a 10 m e o Monitor converte para 2 m, a altura da barra de que a bula fala
(FAO-56, × 0,748; o mesmo `vento_2m` do `agents`). Só a inversão (`inversao_vento_max_kmh`) usa esse vento a 2 m
em calma; o Monitor não tem limiar de vento mínimo (o mínimo da bula é lido pelo Avaliador). A rajada a 2 m é uma
**estimativa**: é a rajada a 10 m escalada pelo mesmo fator do perfil logarítmico, e perto do solo a rajada tende a ser
um pouco maior do que esse fator indica. A leitura observada (`/observado`, reanálise ou estação INMET, ambas a 10 m)
também sai a 2 m na resposta, para a tela não misturar alturas; o valor bruto fica em `clima.observacoes`.

`rajada`, `delta_t` e `inversao` só abrem incidente quando o talhão tem uma OS que o Contexto do `agents` vai
encontrar: OS de aplicação (com produto) com status `agendada` e início previsto entre `max(agora, inicio − 24 h)`
e `fim` do gatilho (`fim` = última hora acima do limite), a mesma busca de `erp_mcp.leitura.listar_os` com o filtro
de status do Contexto. OS `reagendada` não conta, porque o Contexto não a considera. Sem OS, a condição aparece só
no mapa de risco (`/risco`), e num incidente aberto por chuva ela sai da lista de gatilhos. Motivo: rajada acima do
limite e noite com inversão acontecem quase todo dia, e sem esse filtro o Monitor reabriria um incidente a cada
ciclo de 30 min; com um critério mais largo que o do Contexto, o `agents` encerraria o incidente como "nenhuma OS"
e o Monitor o reabriria no ciclo seguinte. Se a tabela de OS não puder ser lida, vale "sem OS" e o Monitor avisa
uma vez no log.
`chuva`, `vento` e `umidade` abrem incidente como antes, com ou sem OS.

`temperatura` já tem fator de perda mas ainda não tem limiar nem gatilho. Os limiares são lidos das variáveis de ambiente
`LIMIAR_CHUVA_MM`, `LIMIAR_VENTO_KMH`, `LIMIAR_UMIDADE_MIN_PCT`, `JANELA_RISCO_H`,
`LIMIAR_RAJADA_KMH`, `LIMIAR_DELTA_T_MIN_C`, `LIMIAR_DELTA_T_MAX_C` e `LIMIAR_INVERSAO_VENTO_MAX_KMH`.

Fatores de perda (`base_agricola/dados/fatores_perda.yaml`): `vento`, `umidade`, `temperatura` e `atraso`
para fungicida, herbicida e inseticida. Todos são **estimativas parametrizadas** (nenhuma fonte lida quantifica
a perda), e `atraso` é a fração do valor perdida por dia de demora. Só o piso de 17% de perda de depósito (Embrapa CT 73)
serve de ordem de grandeza para umidade e temperatura. Apresentar assim no pitch.

## Disponibilidade no Open-Meteo

Verificado em 2026-11-17 com uma chamada real à API pública (sem chave, modelo `best_match`) para Rio Verde (GO), -17.79/-50.92, `forecast_days=16`: `temperature_80m` e `temperature_120m` vêm preenchidas, assim como `wind_gusts_10m`, `wind_direction_10m`, `cloud_cover` e `is_day`. Nenhum valor nulo em nenhum campo, horário (384 h) ou diário (16 dias), nessa gravação (`monitor/tests/fixtures/open_meteo.json`). Mesmo assim o parse trata todos como opcionais: se vierem nulos ou ausentes, a inversão cai na heurística de noite, céu limpo (`cloud_cover` < 30%) e vento fraco, e sem `cloud_cover`/`is_day` fica `None`.
