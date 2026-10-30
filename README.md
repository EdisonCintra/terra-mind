# TerraMind

Agente que protege operações agrícolas agendadas contra eventos climáticos extremos: o Monitor detecta a chuva prevista, a ordem de serviço em risco é suspensa e um plano de reagendamento vai para aprovação do gerente.

Monorepo com um serviço por pasta. Planos de implementação em [docs/plans/](docs/plans/).

| Pasta | Frente | O que é |
| --- | --- | --- |
| [agents/](agents/) | Gustavo | Workflow ADK do incidente, API na porta 8000 ([README](agents/README.md)) |

Contratos entre as frentes: [docs/contratos.md](docs/contratos.md).
