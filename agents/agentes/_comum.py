"""O que os quatro agentes compartilham. As instruções são funções do estado (`InstructionProvider`):
o contexto entra como JSON montado em código, sem template `{chave}` do ADK (que quebraria com as
chaves do JSON).
"""

import json
import logging
from typing import Any

from google.adk import Agent
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import BaseModel, ValidationError

from agents.config import NomeAgente
from agents.deps import Dependencias
from agents.modelos import Incidente, para_datetime
from agents.rules.janelas import FUSO
from agents.rules.previsao import previsao_da_os

logger = logging.getLogger(__name__)

REGRAS_GERAIS = """Você faz parte do TerraMind, que protege operações agrícolas agendadas contra
eventos climáticos.
Unidades: chuva em mm, vento em km/h, umidade em %, temperatura em °C, tempo em horas. Fuso America/Sao_Paulo.
Responda sempre em português. Nunca invente números: use só os dados abaixo e o que as ferramentas
devolverem."""

# Mensagem que o nó anterior passa como entrada do agente; o contexto real está na instrução.
ENTRADA_LLM = "Execute a tarefa descrita nas instruções."

# Rodadas de ferramenta por execução do agente. Na última o modelo só pode responder; depois dela,
# falha (a mesma marca de um erro do modelo).
MAX_RODADAS_FERRAMENTA = 6
SAIDA_FINAL = "set_model_response"  # ferramenta que o ADK acrescenta para a resposta no schema


def j(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, indent=1, default=str)


def previsao_por_os(estado: Any) -> dict[int, dict]:
    """A previsão no horário de cada OS em risco (extremos na aplicação e chuva depois do fim)."""
    horas = Incidente.model_validate(estado["incidente"]).horas
    return {
        o["os_id"]: previsao_da_os(horas, para_datetime(o["inicio_previsto"]), o["duracao_horas"])
        for o in estado.get("os_em_risco") or []
    }


def os_resumidas(estado: Any) -> list[dict]:
    campos = (
        "os_id",
        "talhao_id",
        "talhao",
        "cultura",
        "estagio",
        "tipo_operacao",
        "produto",
        "tipo_insumo",
        "dose_por_ha",
        "unidade",
        "inicio_previsto",
        "duracao_horas",
        "maquina_id",
    )
    resultado = []
    for o in estado.get("os_em_risco") or []:
        item = {k: o.get(k) for k in campos}
        item["inicio_previsto"] = para_datetime(o["inicio_previsto"]).astimezone(FUSO).strftime("%d/%m %H:%M")
        resultado.append(item)
    return resultado


def chave_falha(nome_agente: str) -> str:
    return f"falha_{nome_agente}"


def _resposta(texto: str) -> LlmResponse:
    return LlmResponse(content=types.ModelContent(parts=[types.Part.from_text(text=texto)]))


def _texto_json(texto: str) -> str:
    texto = texto.strip()
    if texto.startswith("```"):
        texto = texto.strip("`").removeprefix("json").strip()
    return texto


def protecoes(nome_agente: str, saida_vazia: str, schema: type[BaseModel] | None = None) -> dict:
    """Callbacks que impedem uma falha do LLM ou de uma ferramenta de derrubar o workflow.

    O agente roda direto como nó do grafo; uma exceção ali encerraria o workflow inteiro.
    - Ferramenta que levanta (`on_tool_error_callback`) devolve `{"ok": False, "erro": ...}` ao LLM.
    - Erro do modelo (`on_model_error_callback`) ou resposta final fora do `schema`
      (`after_model_callback`) viram `saida_vazia`. Para agentes com `schema`, o motivo vai também
      para `state["falha_<agente>"]`, que o nó de roteamento seguinte lê para decidir (decisão A5);
      agentes de texto só seguem com a `saida_vazia`.
    - Loop de ferramentas (`before_model_callback`): depois de `MAX_RODADAS_FERRAMENTA` rodadas o
      modelo só pode responder; se ainda chamar ferramenta, vira `saida_vazia` como acima.
    - Resposta por `set_model_response` (`after_tool_callback`): convertida para JSON pelo `schema`.
    """
    chave = chave_falha(nome_agente)

    def _marcar(callback_context, motivo: str) -> LlmResponse:
        if schema is not None:
            callback_context.state[chave] = motivo
        return _resposta(saida_vazia)

    async def limitar_rodadas(callback_context, llm_request):
        # Com `include_contents="none"`, `contents` traz só esta execução do agente.
        rodadas = sum(
            1
            for c in llm_request.contents or []
            if c.role == "model" and any(p.function_call for p in c.parts or [])
        )
        if rodadas > MAX_RODADAS_FERRAMENTA:
            return _marcar(callback_context, f"{rodadas} rodadas de ferramenta sem resposta final")
        if rodadas == MAX_RODADAS_FERRAMENTA:
            for tool in llm_request.config.tools or []:
                tool.function_declarations = [
                    d for d in tool.function_declarations or [] if d.name == SAIDA_FINAL
                ]
            llm_request.config.tools = [
                t for t in llm_request.config.tools or [] if t.function_declarations
            ] or None
            llm_request.append_instructions(
                ["Você já consultou o suficiente. Não busque mais nada: dê agora a resposta final."]
            )
        return None

    async def ferramenta_errou(tool, args, tool_context, error):
        logger.warning("ferramenta %s falhou: %s", tool.name, error)
        return {"ok": False, "erro": f"{type(error).__name__}: {error}"}

    async def modelo_errou(callback_context, llm_request, error):
        return _marcar(callback_context, f"{type(error).__name__}: {error}")

    async def conferir_resposta(callback_context, llm_response: LlmResponse):
        conteudo = llm_response.content
        if schema is None or llm_response.partial or conteudo is None or not conteudo.parts:
            return None
        if any(p.function_call for p in conteudo.parts):
            return None
        texto = "".join(p.text for p in conteudo.parts if p.text and not p.thought)
        if not texto.strip():
            return None
        try:
            schema.model_validate_json(_texto_json(texto))
        except ValidationError as e:
            return _marcar(callback_context, f"saída fora do schema: {e.errors()[0]['msg']}")
        return None

    async def saida_em_json(tool, args, tool_context, tool_response):
        # O `set_model_response` do ADK guarda `model_dump()` sem `mode="json"` e depois faz
        # `json.dumps`, que quebra com o `datetime` do `PlanoProposto` (e a sessão no Postgres também).
        resultado = tool_context.actions.set_model_response
        if schema is None or tool.name != SAIDA_FINAL or not isinstance(resultado, dict):
            return None
        em_json = schema.model_validate(resultado).model_dump(mode="json", exclude_none=True)
        tool_context.actions.set_model_response = em_json
        return em_json

    return {
        "before_model_callback": limitar_rodadas,
        "after_tool_callback": saida_em_json,
        "on_tool_error_callback": ferramenta_errou,
        "on_model_error_callback": modelo_errou,
        "after_model_callback": conferir_resposta,
    }


def criar_agente(
    deps: Dependencias,
    modelo: NomeAgente,
    *,
    name: str,
    saida_vazia: str,
    output_schema: type[BaseModel] | None = None,
    **campos: Any,
) -> Agent:
    """Agente no formato comum: contexto só pela instrução, entrada fixa e proteções.

    Sem `mode`: como nó do grafo o padrão é `single_turn` (doc de workflows), e como agente raiz no
    `adk web` a doc pede para não definir `mode`.
    """
    return Agent(
        name=name,
        model=deps.modelo(modelo),
        output_schema=output_schema,
        include_contents="none",
        **protecoes(name, saida_vazia, output_schema),
        **campos,
    )


def raiz_para_adk(criar) -> Agent:
    """`root_agent` de uma pasta, montado só quando o `adk web`/`adk run` pede (o serviço não usa)."""
    from agents.agentes._demo import agente_sozinho

    return agente_sozinho(criar)
