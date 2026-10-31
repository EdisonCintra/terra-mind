"""Uma pasta por agente, no padrão do ADK (https://adk.dev/get-started/python/).

Cada subpasta tem `__init__.py` e `agent.py`. O `agent.py` define `criar(deps)`, usado pelo workflow
do serviço, e exporta `root_agent` (ou `app`) para o `adk web` e o `adk run`, montado com a fazenda
simulada de `agents.simulacao`:

    cd agents
    uv run adk web agents/agentes             # escolha o agente no canto superior esquerdo
    uv run adk run agents/agentes/terramind

`terramind` é o fluxo inteiro; os outros rodam sozinhos, com o estado do cenário de chuva já preenchido.
"""
