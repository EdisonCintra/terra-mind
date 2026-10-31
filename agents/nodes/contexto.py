"""Nó Contexto: carrega o incidente e lista as OS agendadas que a janela de risco alcança."""

import asyncio
import re
from datetime import timedelta

from google.adk import Context, Event
from google.genai import types

from agents.agentes._comum import ENTRADA_LLM
from agents.clientes.erp import ErpIndisponivel
from agents.deps import Dependencias
from agents.modelos import Incidente, OsEmRisco, como_json, para_datetime
from agents.nodes.comum import auditar

# Uma aplicação feita até este tempo antes da chuva ainda sofre com ela (lavagem, lixiviação).
ALCANCE_ANTES_DA_CHUVA = timedelta(hours=24)

# Tipo de máquina que cada operação usa, quando o ERP não manda `maquina_tipo` na OS.
TIPO_MAQUINA_POR_OPERACAO = {"adubacao": "distribuidor", "pulverizacao": "pulverizador"}


def janela_de_risco(incidente: Incidente, agora, padrao_h: int):
    if incidente.gatilhos:
        return min(g.inicio for g in incidente.gatilhos), max(g.fim for g in incidente.gatilhos)
    return agora, agora + timedelta(hours=padrao_h)


def para_os_em_risco(linha: dict, talhao: dict) -> OsEmRisco:
    return OsEmRisco(
        os_id=linha["id"],
        talhao_id=linha["talhao_id"],
        talhao=linha.get("talhao") or talhao.get("nome") or f"Talhão {linha['talhao_id']}",
        area_ha=float(linha.get("area_ha") or talhao.get("area_ha") or 0),
        cultura=talhao.get("cultura"),
        estagio=talhao.get("estagio_fenologico"),
        tipo_operacao=linha["tipo_operacao"],
        insumo_id=linha.get("insumo_id"),
        produto=linha.get("produto"),
        tipo_insumo=linha.get("tipo_insumo") or linha.get("tipo"),
        dose_por_ha=None if linha.get("dose_por_ha") is None else float(linha["dose_por_ha"]),
        unidade=linha.get("unidade"),
        preco_unitario=None if linha.get("preco_unitario") is None else float(linha["preco_unitario"]),
        inicio_previsto=para_datetime(linha["inicio_previsto"]),
        duracao_horas=float(linha.get("duracao_horas") or 8),
        maquina_id=linha.get("maquina_id"),
        tipo_maquina=linha.get("maquina_tipo") or TIPO_MAQUINA_POR_OPERACAO.get(linha["tipo_operacao"]),
        operador_id=linha.get("operador_id"),
    )


def id_da_mensagem(node_input) -> int | None:
    """Primeiro número da mensagem do usuário ("incidente 7" → 7). Usado no `adk web` / `adk run`."""
    texto = node_input if isinstance(node_input, str) else ""
    if isinstance(node_input, types.Content):
        texto = "".join(p.text or "" for p in node_input.parts or [])
    achado = re.search(r"\d+", texto)
    return int(achado.group()) if achado else None


def criar_contexto(deps: Dependencias, id_da_entrada: bool = False):
    """O Orquestrador põe o id do incidente no estado ao criar a sessão. Com `id_da_entrada`
    (só na demonstração do `adk web` / `adk run`), o id vem do texto digitado."""

    async def contexto(ctx: Context, node_input):
        incidente_id = ctx.state.get("incidente_id")
        if incidente_id is None and id_da_entrada:
            incidente_id = id_da_mensagem(node_input)
            ctx.state["incidente_id"] = incidente_id
        if incidente_id is None:
            ctx.state["motivo_encerramento"] = "mensagem sem número de incidente"
            return Event(route="sem_os")
        incidente = await deps.repo.carregar_incidente(incidente_id)
        if incidente is None:
            ctx.state["motivo_encerramento"] = "incidente inexistente"
            return Event(route="sem_os")
        agora = deps.relogio()
        inicio, fim = janela_de_risco(incidente, agora, deps.settings.janela_padrao_h)
        try:
            linhas, talhao = await asyncio.gather(
                deps.erp.listar_os(max(agora, inicio - ALCANCE_ANTES_DA_CHUVA), fim, incidente.talhao_id),
                deps.erp.consultar_talhao(incidente.talhao_id),
            )
        except ErpIndisponivel as e:
            ctx.state["incidente"] = como_json(incidente)
            ctx.state["os_em_risco"] = []
            ctx.state["motivo_manual"] = {"codigo": "erp_indisponivel", "detalhe": str(e)}
            return Event(route="falhou")
        em_risco = [para_os_em_risco(linha, talhao) for linha in linhas if linha.get("status") == "agendada"]
        ctx.state["incidente"] = como_json(incidente)
        ctx.state["os_em_risco"] = como_json(em_risco)
        await auditar(
            deps,
            ctx,
            "contexto",
            "listar_os_em_risco",
            depois={"janela": [inicio.isoformat(), fim.isoformat()], "os": [o.os_id for o in em_risco]},
        )
        if not em_risco:
            ctx.state["motivo_encerramento"] = "nenhuma OS agendada na janela de risco"
            return Event(route="sem_os")
        return Event(route="com_os", output=ENTRADA_LLM)

    return contexto
