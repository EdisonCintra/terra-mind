"""Ferramentas expostas aos LLMs. Devolvem `{"ok": True, ...}` ou `{"ok": False, "erro": ...}`.

Exceção numa ferramenta vira `{"ok": False, "erro": ...}` pelo `on_tool_error_callback` dos agentes
(`agents.agentes._comum.protecoes`). São closures sobre `Dependencias`, para os testes trocarem ERP
e conhecimento por fakes. As escritas no ERP não aparecem aqui: só os nós em código escrevem.
"""

from google.adk.tools.tool_context import ToolContext

from agents.deps import Dependencias
from agents.modelos import para_datetime
from agents.rules.impacto import calcular_impacto as _calcular_impacto
from agents.rules.validador import ContextoPlano
from agents.rules.validador import janelas_validas as _janelas_validas


def criar_buscar_trechos(deps: Dependencias):
    async def buscar_trechos(consulta: str, produto: str | None = None, secao: str | None = None) -> dict:
        """Busca trechos de bulas (Agrofit) e referências técnicas (Embrapa) na base de conhecimento.

        Args:
            consulta: o que procurar, em português (ex.: "chuva após a aplicação", "velocidade do vento").
            produto: nome exato do produto, como aparece na OS. Informe sempre, para achar limites.
            secao: em bulas, use "condicoes_climaticas" para limites de clima; referências usam "geral".

        Returns:
            {"ok": true, "trechos": [{"texto", "fonte_doc", "pagina", "secao", "produto"}]}. Lista vazia
            significa que o documento não traz esse assunto: não invente um limite.
        """
        trechos = await deps.conhecimento.buscar_trechos(consulta, produto=produto, secao=secao)
        return {
            "ok": True,
            "trechos": [
                {k: t.get(k) for k in ("texto", "fonte_doc", "pagina", "secao", "produto")} for t in trechos
            ],
        }

    return buscar_trechos


def criar_consultar_talhao(deps: Dependencias):
    async def consultar_talhao(talhao_id: int) -> dict:
        """Dados do talhão: cultura, estágio fenológico, área (ha), tipo de solo e coordenadas."""
        return {"ok": True, "talhao": await deps.erp.consultar_talhao(talhao_id)}

    return consultar_talhao


def criar_listar_os(deps: Dependencias):
    async def listar_os(inicio: str, fim: str, talhao_id: int | None = None) -> dict:
        """Ordens de serviço com início previsto entre `inicio` e `fim`.

        Datas em ISO 8601 com fuso, ex.: 2026-11-03T07:00:00-03:00.
        """
        return {
            "ok": True,
            "os": await deps.erp.listar_os(para_datetime(inicio), para_datetime(fim), talhao_id),
        }

    return listar_os


def criar_disponibilidade_maquinas(deps: Dependencias):
    async def disponibilidade_maquinas(inicio: str, fim: str, tipo: str | None = None) -> dict:
        """Máquinas livres (sem OS agendada ou reagendada) no intervalo, em ISO 8601 com fuso.

        `tipo` filtra, ex.: "distribuidor".
        """
        maquinas = await deps.erp.disponibilidade_maquinas(para_datetime(inicio), para_datetime(fim), tipo)
        return {"ok": True, "maquinas": maquinas}

    return disponibilidade_maquinas


def criar_listar_operadores(deps: Dependencias):
    async def listar_operadores() -> dict:
        """Operadores da fazenda."""
        return {"ok": True, "operadores": await deps.erp.listar_operadores()}

    return listar_operadores


def criar_janelas_validas(deps: Dependencias):
    async def janelas_validas(os_id: int, tool_context: ToolContext) -> dict:
        """Próximas janelas válidas para remarcar uma OS suspensa, calculadas em código.

        Cada janela respeita o limite que o Avaliador citou, cabe na previsão de 5 dias, começa
        entre 6h e 18h e tem máquina livre. Proponha só datas desta lista.

        Returns:
            {"ok": true, "janelas": [{"inicio": ISO 8601, "maquinas_livres": [ids, a atual primeiro]}]}
        """
        contexto = ContextoPlano.do_estado(tool_context.state)
        if os_id not in contexto.violacoes_por_os:
            return {"ok": False, "erro": f"OS {os_id} não tem violação avaliada neste incidente"}
        return {"ok": True, "janelas": await _janelas_validas(contexto, os_id, deps.erp, deps.relogio())}

    return janelas_validas


def criar_calcular_impacto(deps: Dependencias):
    async def calcular_impacto(os_id: int, condicao: str, tool_context: ToolContext) -> dict:
        """Perda em R$ se a OS for aplicada sob a condição: área × dose × preço × fator citado.

        Args:
            os_id: a OS.
            condicao: "chuva", "vento", "umidade" ou "temperatura".
        """
        os = ContextoPlano.do_estado(tool_context.state).os_por_id.get(os_id)
        if os is None:
            return {"ok": False, "erro": f"OS {os_id} não está em risco neste incidente"}
        impacto = await _calcular_impacto(os, condicao, deps.conhecimento)
        return {"ok": True, "impacto": impacto.model_dump(mode="json")}

    return calcular_impacto
