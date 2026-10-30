"""TerraMind: o workflow inteiro do incidente, com a fazenda simulada (sem Postgres, MCP nem Telegram).

Digite um número de incidente (ex.: "1"). Cada número novo recria a fazenda: 80 mm de chuva nas
próximas 36 h e uma OS de adubação com ureia daqui a 20 h. Na pausa de aprovação, responda com
`{"resposta": "SIM", "respondido_por": "gustavo"}` (ou "NAO", "OPCOES").

O serviço não usa este módulo: ele monta o mesmo workflow com `agents.workflow.criar_app` e os
clientes reais.
"""


def __getattr__(nome: str):
    if nome in ("app", "root_agent"):
        from agents.agentes._demo import app_terramind

        app = app_terramind()
        return app if nome == "app" else app.root_agent
    raise AttributeError(nome)
