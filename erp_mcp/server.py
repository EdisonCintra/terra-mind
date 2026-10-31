"""Servidor MCP do mini-ERP (contratos 4 e 5): http://erp-mcp:8030/mcp, streamable HTTP.

`janelas_validas` NÃO é ferramenta daqui: é código determinístico em `agents/rules/` (D4/A6).
"""

from mcp.server.fastmcp import FastMCP

from erp_mcp import escrita, leitura

mcp = FastMCP("erp", host="0.0.0.0", port=8030)

FERRAMENTAS = [
    leitura.listar_os,
    leitura.detalhar_os,
    leitura.disponibilidade_maquinas,
    leitura.listar_operadores,
    leitura.consultar_talhao,
    leitura.leituras_solo,
    escrita.suspender_os,
    escrita.reagendar_os,
    escrita.alocar_maquina,
    escrita.reservar_estoque,
]

for funcao in FERRAMENTAS:
    mcp.tool()(funcao)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
