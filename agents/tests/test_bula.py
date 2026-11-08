"""G4: Pergunte à bula. A citação é conferida em código contra o que a busca devolveu."""

from fastapi.testclient import TestClient

from agents.api import Servicos, criar_api
from agents.bula import FALHA, PergunteBula
from agents.modelos import CitacaoBula, RespostaBula
from agents.rules.citacao import NAO_ENCONTRADO, conferir, normalizar
from agents.tests.fakes import TRECHO_BULA, bula_que_cita, chamar, modelos_padrao, montar_cenario

VISTO = {"texto": TRECHO_BULA, "fonte_doc": "bula-abacus-hc", "pagina": 7, "produto": "Abacus HC"}


def resposta(trecho: str, pagina: int = 7, fonte: str = "bula-abacus-hc") -> RespostaBula:
    return RespostaBula(
        encontrou=True,
        resposta="Chuva em até 4 horas após a aplicação pode afetar o desempenho.",
        citacoes=[CitacaoBula(trecho=trecho, fonte_doc=fonte, pagina=pagina)],
    )


# ---------------------------------------------------------------- conferência em código


def test_citacao_literal_passa_e_ganha_o_produto():
    r = conferir(resposta(TRECHO_BULA), [VISTO])
    assert r.encontrou and r.citacoes[0].pagina == 7 and r.citacoes[0].produto == "Abacus HC"


def test_citacao_com_reticencias_e_quebra_de_linha_passa():
    cortada = "A ocorrência de chuvas dentro de um período ... após a\naplicação pode afetar o desempenho"
    assert conferir(resposta(cortada), [VISTO]).encontrou


def test_acento_e_maiusculas_nao_quebram_a_conferencia():
    assert normalizar("Período  de QUATRO (4) horas") == normalizar("periodo de quatro (4)\nhoras")


def test_trecho_reescrito_pelo_llm_e_descartado():
    r = conferir(resposta("A chuva até 4 horas depois da aplicação estraga o produto."), [VISTO])
    assert not r.encontrou and r.resposta == NAO_ENCONTRADO and r.descartadas == 1


def test_pagina_ou_fonte_que_nao_vieram_da_busca_sao_descartadas():
    assert not conferir(resposta(TRECHO_BULA, pagina=9), [VISTO]).encontrou
    assert not conferir(resposta(TRECHO_BULA, fonte="bula-ativum"), [VISTO]).encontrou


def test_pedaco_curto_demais_nao_identifica_o_trecho():
    assert not conferir(resposta("4 horas"), [VISTO]).encontrou


def test_sem_resposta_ou_nao_encontrado_vira_a_bula_nao_traz_isso():
    assert conferir(None, [VISTO]).resposta == NAO_ENCONTRADO
    nada = RespostaBula(encontrou=False, resposta="Não achei.")
    assert conferir(nada, [VISTO]).resposta == NAO_ENCONTRADO


def test_schema_exige_citacao_quando_encontrou():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RespostaBula(encontrou=True, resposta="10 km/h", citacoes=[])


# ---------------------------------------------------------------- agente com LLM roteirizado


async def test_resposta_cita_a_pagina_que_a_busca_devolveu():
    c = montar_cenario()
    r = await PergunteBula(c.deps).perguntar("Quanto tempo sem chuva o Abacus HC precisa?", "Abacus HC")
    assert r.encontrou and r.erro is None
    assert [(x.fonte_doc, x.pagina) for x in r.citacoes] == [("bula-abacus-hc", 7)]
    assert c.conhecimento.buscas[-1][1] == "Abacus HC"
    [auditada] = [a for a in c.repo.auditoria if a["acao"] == "responder_pergunta"]
    assert auditada["incidente_id"] is None and auditada["depois"]["encontrou"]


async def test_pagina_inventada_pelo_llm_nao_chega_ao_usuario():
    c = montar_cenario(modelos_padrao(bula=[bula_que_cita(pagina=12)]))
    r = await PergunteBula(c.deps).perguntar("Qual o limite de chuva do Abacus HC?", "Abacus HC")
    assert not r.encontrou and r.resposta == NAO_ENCONTRADO and r.descartadas == 1 and r.citacoes == []


async def test_sem_trecho_a_resposta_e_a_bula_nao_traz_isso():
    sem_nada = {"encontrou": False, "resposta": "A bula não traz isso.", "citacoes": []}
    c = montar_cenario(
        modelos_padrao(bula=[chamar("buscar_na_bula", consulta="preço", produto="Abacus HC"), sem_nada])
    )
    r = await PergunteBula(c.deps).perguntar("Qual o preço do Abacus HC?", "Abacus HC")
    assert not r.encontrou and r.resposta == NAO_ENCONTRADO and r.erro is None


async def test_llm_fora_do_ar_avisa_sem_inventar():
    c = montar_cenario(modelos_padrao(bula=[RuntimeError("OpenAI fora do ar")]))
    r = await PergunteBula(c.deps).perguntar("Qual o limite de vento do Abacus HC?", "Abacus HC")
    assert not r.encontrou and r.resposta == FALHA and r.erro and r.citacoes == []


async def test_resposta_fora_do_schema_nao_passa():
    sem_citacao = {"encontrou": True, "resposta": "Pode aplicar até 10 km/h.", "citacoes": []}
    c = montar_cenario(modelos_padrao(bula=[sem_citacao]))
    r = await PergunteBula(c.deps).perguntar("Qual o limite de vento do Abacus HC?", "Abacus HC")
    assert not r.encontrou and r.citacoes == []


async def test_perguntas_nao_compartilham_trechos_vistos():
    """A segunda pergunta não pode citar um trecho que só a primeira buscou."""
    c = montar_cenario(
        modelos_padrao(
            bula=[
                bula_que_cita(),
                {
                    "encontrou": True,
                    "resposta": "x",
                    "citacoes": [{"trecho": TRECHO_BULA, "fonte_doc": "bula-abacus-hc", "pagina": 7}],
                },
            ]
        )
    )
    bula = PergunteBula(c.deps)
    assert (await bula.perguntar("Chuva no Abacus HC?", "Abacus HC")).encontrou
    segunda = await bula.perguntar("E o vento?", "Abacus HC")  # responde sem buscar nada
    assert not segunda.encontrou


# ---------------------------------------------------------------- rota da API


class _Orq:
    async def aguardar(self):
        return None


def _cliente(bula):
    async def fabrica():
        return Servicos(orquestrador=_Orq(), bula=bula)

    return TestClient(criar_api(fabrica))


def test_rota_devolve_resposta_conferida():
    with _cliente(PergunteBula(montar_cenario().deps)) as c:
        r = c.post("/bula/perguntar", json={"pergunta": "Chuva depois do Abacus HC?", "produto": "Abacus HC"})
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["encontrou"] and corpo["citacoes"][0]["pagina"] == 7
        assert c.post("/bula/perguntar", json={"pergunta": ""}).status_code == 422


def test_rota_sem_o_servico_responde_503():
    with _cliente(None) as c:
        assert c.post("/bula/perguntar", json={"pergunta": "Qual o vento?"}).status_code == 503


# ---------------------------------------------------------------- as 20 perguntas de teste


def test_arquivo_tem_20_perguntas_com_gabarito_e_pagina():
    from agents.avaliar_bula import carregar

    casos = carregar()
    assert len(casos) == 20
    for c in casos:
        assert c.gabarito
        if c.encontra:
            assert c.fonte and c.paginas and c.deve_conter, c.pergunta
    assert any(not c.encontra for c in casos)  # ao menos uma fora da bula


def test_julgamento_exige_pagina_certa_e_numeros():
    from agents.avaliar_bula import carregar, julgar
    from agents.modelos import ResultadoBula

    vento = carregar()[0]  # Abacus HC com 12 km/h → p. 7, "10" e "não"
    certa = ResultadoBula(
        encontrou=True,
        resposta="Não. A bula pede vento entre 2 e 10 km/h.",
        citacoes=[CitacaoBula(trecho="x" * 20, fonte_doc="bula-abacus-hc", pagina=7)],
    )
    assert julgar(vento, certa) == []
    pagina_errada = certa.model_copy(
        update={"citacoes": [certa.citacoes[0].model_copy(update={"pagina": 5})]}
    )
    assert julgar(vento, pagina_errada)
    sem_numero = certa.model_copy(update={"resposta": "Não, está acima do limite."})
    [problema] = julgar(vento, sem_numero)
    assert problema.startswith("resposta sem") and "10" in problema


async def test_conteudo_vazio_do_modelo_tenta_de_novo():
    """O gpt devolve às vezes conteúdo vazio (MODEL_RETURNED_NO_CONTENT); a 2ª tentativa responde."""
    from google.genai import types

    c = montar_cenario(modelos_padrao(bula=[types.Part(text=""), bula_que_cita()]))
    r = await PergunteBula(c.deps).perguntar("Quanto tempo sem chuva o Abacus HC precisa?", "Abacus HC")
    assert r.encontrou and r.citacoes[0].pagina == 7
    assert c.deps.modelos["bula"].chamadas == 3  # vazio, depois busca + resposta


def test_ordinal_da_bula_nao_vira_letra():
    """A Brucia escreve "30ºC"; sem o cuidado, o NFKD transformava em "30oc" e o 30 sumia."""
    import re

    assert re.search(r"\b30\b", normalizar("inferior a 30ºC"))
    assert normalizar("30ºC") == normalizar("30°C")


# ---------------------------------------------------------------- nos canais (Telegram)


def test_texto_para_canal_traz_bula_pagina_e_trecho():
    from agents.bula import texto_para_canal
    from agents.modelos import ResultadoBula

    r = ResultadoBula(
        encontrou=True,
        resposta="Chuva em até 4 horas atrapalha.",
        citacoes=[CitacaoBula(trecho=TRECHO_BULA, fonte_doc="bula-abacus-hc", pagina=7, produto="Abacus HC")],
    )
    texto = texto_para_canal(r)
    assert texto.startswith("Chuva em até 4 horas") and "Fonte: bula Abacus HC, p. 7" in texto
    assert "quatro (4) horas" in texto
    assert texto_para_canal(ResultadoBula(encontrou=False, resposta=NAO_ENCONTRADO)) == NAO_ENCONTRADO


async def test_resposta_no_canal_audita_quem_perguntou():
    c = montar_cenario()
    texto = await PergunteBula(c.deps).responder_no_canal("Chuva depois do Abacus HC?", "Gerente Teste")
    assert "p. 7" in texto
    [auditada] = [a for a in c.repo.auditoria if a["acao"] == "responder_pergunta"]
    assert auditada["depois"]["encontrou"]


async def test_produto_escrito_a_mao_acha_a_bula_certa():
    """No celular escrevem "abacus": o filtro exato não acha, a busca aproximada acha o Abacus HC."""
    from agents.agentes.pergunte_bula.agent import criar_buscar_na_bula

    c = montar_cenario()
    buscar = criar_buscar_na_bula(c.deps)
    contexto = type("Ctx", (), {"state": {}})()
    r = await buscar("chuva após a aplicação", contexto, produto="abacus")
    assert [t["produto"] for t in r["trechos"]] == ["Abacus HC"]
    assert contexto.state["trechos_vistos"][0]["pagina"] == 7
    nada = await buscar("chuva", contexto, produto="Produto Inexistente")
    assert nada["trechos"] == []
