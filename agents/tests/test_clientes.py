import httpx
import pytest
import respx
from mcp.types import CallToolResult, TextContent

from agents.canal import CanalLog, criar_canal, normalizar_resposta
from agents.clientes.conhecimento import ConhecimentoHttp, ConhecimentoIndisponivel
from agents.clientes.erp import interpretar_resultado
from agents.clientes.repositorio import url_asyncpg

URL = "http://conhecimento:8010"


@respx.mock
async def test_buscar_trechos_envia_filtros():
    rota = respx.post(f"{URL}/trechos/busca").respond(200, json=[{"texto": "x", "pagina": 4}])
    trechos = await ConhecimentoHttp(URL).buscar_trechos("chuva", produto="Ureia 46%")
    assert trechos[0]["pagina"] == 4
    assert rota.calls.last.request.content == b'{"consulta":"chuva","limite":3,"produto":"Ureia 46%"}'


@respx.mock
async def test_fator_inexistente_volta_none():
    respx.get(f"{URL}/fatores-perda").respond(404, json={"detail": "x"})
    assert await ConhecimentoHttp(URL).fator_perda("fertilizante", "chuva") is None


@respx.mock
async def test_servico_fora_do_ar_vira_indisponivel():
    respx.post(f"{URL}/trechos/busca").mock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(ConhecimentoIndisponivel):
        await ConhecimentoHttp(URL).buscar_trechos("chuva")


def test_mcp_lista_embrulhada_em_result():
    r = CallToolResult(
        content=[TextContent(type="text", text="[]")], structuredContent={"result": [{"id": 1}]}
    )
    assert interpretar_resultado(r) == [{"id": 1}]


def test_mcp_dict_estruturado():
    r = CallToolResult(content=[], structuredContent={"ok": False, "erro": "OS 1 não está agendada"})
    assert interpretar_resultado(r) == {"ok": False, "erro": "OS 1 não está agendada"}


def test_mcp_json_no_texto():
    r = CallToolResult(content=[TextContent(type="text", text='{"ok": true}')])
    assert interpretar_resultado(r) == {"ok": True}


def test_mcp_erro_do_servidor():
    r = CallToolResult(content=[TextContent(type="text", text="boom")], isError=True)
    assert interpretar_resultado(r) == {"ok": False, "erro": "boom"}


def test_url_asyncpg():
    assert url_asyncpg("postgresql://u:p@h:5432/d") == "postgresql+asyncpg://u:p@h:5432/d"
    assert url_asyncpg("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"


def test_sem_pacote_channels_usa_canal_de_log():
    async def ao_responder(r):
        return None

    assert isinstance(criar_canal("telegram", ao_responder), CanalLog)


def test_normaliza_resposta_do_felipe():
    class RespostaFelipe:
        incidente_id, plano_id, resposta, respondido_por = "7", "1", "SIM", "Fulano"

    r = normalizar_resposta(RespostaFelipe())
    assert (r.incidente_id, r.plano_id, r.resposta) == (7, 1, "SIM")


def test_workflow_monta_com_litellm_da_openai():
    from google.adk.models.lite_llm import LiteLlm

    from agents.config import Settings
    from agents.deps import criar_modelo
    from agents.workflow import criar_app
    from tests.fakes import montar_cenario

    settings = Settings(_env_file=None, openai_api_key="sk-teste", model_supervisor="openai/gpt-4o-mini")
    modelo = criar_modelo(settings, "supervisor")
    assert isinstance(modelo, LiteLlm) and modelo.model == "openai/gpt-4o-mini"
    c = montar_cenario()
    c.deps.settings, c.deps.modelos = settings, {}
    assert criar_app(c.deps).root_agent.name == "incidente"
