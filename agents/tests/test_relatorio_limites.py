"""Contrato 26 (limites lidos da bula), G7 (relatório de aplicação), G1 (impactos) e cliente de clima."""

from datetime import timedelta

import httpx
import pymupdf
import pytest
import respx
from fastapi.testclient import TestClient
from google.adk.sessions import InMemorySessionService

from agents.api import Servicos, criar_api
from agents.clientes.clima import ClimaIndisponivel, OpenMeteoClima, delta_t, interpretar
from agents.limites import ExtratorLimites, LimiteExtraido, LimitesExtraidos, conferir
from agents.modelos import HoraPrevisao, LimiteProduto
from agents.relatorio import RelatorioIndisponivel, ServicoRelatorio, resumo_sha256
from agents.rules.conformidade import conferir_aplicacao, resumo_clima, veredito
from agents.rules.limites import limites_efetivos
from agents.runner import Orquestrador
from agents.tests.fakes import AGORA, TRECHO_BULA, chamar, modelos_padrao, montar_cenario
from channels.base import Resposta

VISTO = {"texto": TRECHO_BULA, "fonte_doc": "bula-abacus-hc", "pagina": 7, "produto": "Abacus HC"}
CHUVA = {
    "parametro": "chuva", "sentido": "maximo", "limite": 0, "unidade": "mm", "janela_h": 4,
    "trecho": TRECHO_BULA, "fonte_doc": "bula-abacus-hc", "pagina": 7,
}  # fmt: skip
CHUVA_BULA = LimiteProduto.model_validate(CHUVA)


# ------------------------------------------------------------------ contrato 26: limites da bula
def test_limite_citado_e_aceito_e_o_inventado_e_descartado():
    vento_inventado = {**CHUVA, "parametro": "vento", "limite": 10, "janela_h": None, "unidade": "km/h"}
    pagina_errada = {**CHUVA, "pagina": 12}
    fora_da_busca = {**CHUVA, "trecho": "Não aplicar com ventos acima de 10 km/h em nenhuma hipótese."}
    extraidos = LimitesExtraidos(
        limites=[LimiteExtraido(**x) for x in (CHUVA, vento_inventado, pagina_errada, fora_da_busca)]
    )
    aceitos, descartados = conferir(extraidos, [VISTO])

    assert [(x.parametro, x.limite, x.janela_h, x.pagina) for x in aceitos] == [("chuva", 0, 4, 7)]
    assert len(descartados) == 3
    assert "o número 10 não aparece no trecho" in descartados[0]  # o trecho é real, o número não
    assert "não veio da busca" in descartados[1] and "não veio da busca" in descartados[2]


def test_numero_por_extenso_no_trecho_vale():
    por_extenso = {**VISTO, "texto": "Evitar chuvas nas duas horas seguintes à aplicação do produto."}
    extraido = LimiteExtraido(**{**CHUVA, "janela_h": 2, "trecho": por_extenso["texto"]})
    aceitos, descartados = conferir(LimitesExtraidos(limites=[extraido]), [por_extenso])
    assert len(aceitos) == 1 and not descartados


def _extrator(resposta):
    roteiro = [chamar("buscar_na_bula", consulta="chuva após a aplicação", produto="Abacus HC"), resposta]
    cenario = montar_cenario(modelos_padrao(avaliador=roteiro))
    return cenario, ExtratorLimites(cenario.deps)


async def test_extracao_grava_so_o_que_foi_conferido():
    inventado = {**CHUVA, "parametro": "temperatura", "limite": 30, "janela_h": None}
    cenario, extrator = _extrator({"limites": [CHUVA, inventado]})
    r = await extrator.extrair("Abacus HC")

    assert r.erro is None and [x.parametro for x in r.limites] == ["chuva"]
    assert len(r.descartados) == 1
    assert cenario.repo.limites["Abacus HC"] == r.limites
    assert "extrator_limites:extrair_limites" in cenario.repo.acoes()


async def test_extracao_que_falha_nao_grava_nada():
    cenario = montar_cenario(modelos_padrao(avaliador=[RuntimeError("OpenAI fora do ar")]))
    r = await ExtratorLimites(cenario.deps).extrair("Abacus HC")
    assert r.erro and not r.limites and "Abacus HC" not in cenario.repo.limites


# ------------------------------------------------------------------ G7: conformidade e relatório
def horas_de(inicio, n=30, **campos) -> list[HoraPrevisao]:
    base = {
        "chuva_mm": 0,
        "vento_kmh": 6,
        "umidade_pct": 70,
        "temp_c": 24,
        "delta_t_c": 4,
        "vento_dir_graus": 90,
    }
    return [HoraPrevisao(ts=inicio + timedelta(hours=i), **{**base, **campos}) for i in range(n)]


# Aplicação de ontem, das 07h20 às 10h20 (AGORA = 08h de hoje).
INICIO = AGORA.replace(hour=7, minute=20) - timedelta(days=1)
FIM = INICIO + timedelta(hours=3)


def test_aplicacao_dentro_dos_limites_e_conforme():
    horas = horas_de(INICIO.replace(minute=0))
    itens = conferir_aplicacao(horas, limites_efetivos([CHUVA_BULA]), INICIO, FIM, AGORA)
    assert veredito(itens) is True
    chuva = next(i for i in itens if i.da_bula)
    assert chuva.situacao == "conforme" and chuva.fonte == "bula, p. 7" and not chuva.previsto
    assert "4 h seguintes" in chuva.regra


def test_chuva_depois_da_aplicacao_reprova_com_a_pagina():
    horas = horas_de(INICIO.replace(minute=0))
    horas[5] = horas[5].model_copy(update={"chuva_mm": 6})  # 12h: 1 h depois do fim (07h20–10h20 → até 11h)
    itens = conferir_aplicacao(horas, limites_efetivos([CHUVA_BULA]), INICIO, FIM, AGORA)
    chuva = next(i for i in itens if i.da_bula)
    assert veredito(itens) is False
    assert chuva.situacao == "nao_conforme" and "6.0 mm" in chuva.detalhe


def test_sem_limite_de_bula_o_veredito_fica_em_aberto():
    horas = horas_de(INICIO.replace(minute=0), vento_kmh=25)
    itens = conferir_aplicacao(horas, limites_efetivos([]), INICIO, FIM, AGORA)
    assert veredito(itens) is None  # vento fora da referência genérica aparece, mas não é a bula
    assert any(i.situacao == "nao_conforme" and not i.da_bula for i in itens)


def test_resumo_do_clima_traz_extremos_direcao_e_leituras_de_inicio_e_fim():
    horas = horas_de(INICIO.replace(minute=0))
    horas[0] = horas[0].model_copy(update={"temp_c": 19, "vento_kmh": 3})
    resumo = resumo_clima(horas, INICIO, FIM)
    assert resumo["horas"] == 4  # 07h, 08h, 09h e 10h cobrem 07h20–10h20
    assert (resumo["temp_min_c"], resumo["temp_max_c"]) == (19, 24)
    assert resumo["vento_direcao"] == "L" and resumo["inicial"]["vento_kmh"] == 3
    assert resumo_clima(horas, INICIO + timedelta(days=9), FIM + timedelta(days=9)) is None


def _com_execucao(**extra):
    cenario = montar_cenario()
    cenario.erp.os[1]["status"] = "executada"
    cenario.erp.execucoes[1] = {
        "inicio_real": INICIO.isoformat(),
        "fim_real": FIM.isoformat(),
        "area_aplicada_ha": 118.5,
        "dose_real_por_ha": 0.3,
        "volume_calda_l_ha": 120,
        "modalidade": "terrestre",
        "maquina": "Pulverizador A",
        "operador": "João",
        "receituario_numero": "GO-2026-000123",
        "responsavel_tecnico": "Eng. Agr. Maria Souza",
        "registro_profissional": "CREA-GO 12345",
        **extra,
    }
    cenario.clima.horas = horas_de(INICIO.replace(minute=0), n=40)
    cenario.repo.limites["Abacus HC"] = [CHUVA_BULA]
    return cenario


def _texto(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return " ".join(" ".join(pagina.get_text().split()) for pagina in doc)


async def test_relatorio_da_os_concluida_tem_todos_os_campos_e_o_resumo_confere():
    cenario = _com_execucao()
    registro = await ServicoRelatorio(cenario.deps).gerar(1)

    [guardado] = cenario.repo.relatorios
    assert registro["versao"] == 1 and registro["conforme"] is True
    assert registro["sha256"] == guardado["sha256"] == resumo_sha256(guardado["dados"])
    texto = _texto(guardado["pdf"])
    for esperado in (
        "Talhão 1 - Sede", "milho", "Abacus HC", "118,50 ha", "0,300 L/ha", "120,0 L/ha",
        "Pulverizador A", "João", "GO-2026-000123", "Eng. Agr. Maria Souza", "CREA-GO 12345",
        f"{INICIO:%d/%m/%Y %H:%M}", f"{FIM:%d/%m/%Y %H:%M}", "-17.790000, -50.920000",
        "Conforme", "bula, p. 7", "previsão de teste", registro["sha256"],
    ):  # fmt: skip
        assert esperado in texto, esperado
    assert "Aplicação dentro dos limites da bula" in texto
    assert "relatorio:gerar_relatorio_aplicacao" in cenario.repo.acoes()


async def test_relatorio_de_drone_traz_os_campos_da_portaria_298_e_gerar_de_novo_cria_versao():
    cenario = _com_execucao(
        modalidade="drone", altura_voo_m=3, largura_faixa_m=6, aeronave="PP-123456789", ponta="XR 11001"
    )
    servico = ServicoRelatorio(cenario.deps)
    await servico.gerar(1)
    segundo = await servico.gerar(1)
    texto = _texto(cenario.repo.relatorios[1]["pdf"])
    assert segundo["versao"] == 2 and "versão 2" in texto
    assert all(x in texto for x in ("Portaria MAPA 298/2021", "PP-123456789", "3,0 m", "6,0 m", "XR 11001"))


async def test_relatorio_sai_sem_clima_e_diz_isso():
    cenario = _com_execucao()
    cenario.clima.indisponivel = True
    registro = await ServicoRelatorio(cenario.deps).gerar(1)
    texto = _texto(cenario.repo.relatorios[0]["pdf"])
    assert registro["conforme"] is None
    assert "Sem dado de clima" in texto and "Conformidade com a bula não conferida" in texto


async def test_os_sem_execucao_nao_gera_relatorio():
    cenario = montar_cenario()
    with pytest.raises(RelatorioIndisponivel, match="ainda não tem execução"):
        await ServicoRelatorio(cenario.deps).gerar(1)
    assert cenario.repo.relatorios == []


def test_rota_do_relatorio():
    cenario = _com_execucao()

    async def fabrica():
        class _Orq:
            deps = cenario.deps

            async def aguardar(self):
                return None

        return Servicos(orquestrador=_Orq(), relatorio=ServicoRelatorio(cenario.deps))

    with TestClient(criar_api(fabrica)) as http:
        r = http.post("/relatorios/os/1")
        assert r.status_code == 201 and r.json()["versao"] == 1 and len(r.json()["sha256"]) == 64
        assert http.post("/relatorios/os/2").status_code == 409  # OS sem execução registrada


# ------------------------------------------------------------------ G1: agente.impactos
async def _incidente(cenario):
    orq = Orquestrador(cenario.deps, InMemorySessionService())
    assert await orq.iniciar(7) is True
    return orq


async def test_suspensao_grava_o_impacto_e_o_sim_marca_a_perda_como_evitada():
    cenario = montar_cenario()
    orq = await _incidente(cenario)
    impacto = cenario.repo.impactos[(7, 1)]
    assert impacto["situacao"] == "suspensa"
    assert (impacto["talhao_id"], impacto["produto"], impacto["condicao"]) == (1, "Abacus HC", "chuva")
    assert (impacto["valor_aplicacao_rs"], impacto["fator"], impacto["perda_rs"]) == (6660.0, 0.5, 3330.0)
    assert impacto["fonte"].startswith("estimativa")

    await orq.responder(Resposta(7, 1, "SIM", "gerente"), esperar=True)
    depois = cenario.repo.impactos[(7, 1)]
    assert (depois["situacao"], depois["plano_id"], depois["perda_rs"]) == ("evitada", 1, 3330.0)


async def test_manter_data_marca_o_impacto_como_mantido():
    cenario = montar_cenario()
    orq = await _incidente(cenario)
    await orq.responder(Resposta(7, 1, "NAO", "gerente"), esperar=True)
    assert cenario.repo.impactos[(7, 1)]["situacao"] == "mantida"


async def test_opcoes_esgotadas_marcam_o_impacto_como_recusado():
    cenario = montar_cenario()
    orq = await _incidente(cenario)
    for plano_id in (1, 2, 3):
        await orq.responder(Resposta(7, plano_id, "OPCOES", "gerente"), esperar=True)
    assert cenario.repo.impactos[(7, 1)]["situacao"] == "recusada"


async def test_falha_na_execucao_deixa_o_impacto_em_decisao_manual():
    cenario = montar_cenario()
    cenario.erp.falhar_em.add("reagendar_os")
    orq = await _incidente(cenario)
    await orq.responder(Resposta(7, 1, "SIM", "gerente"), esperar=True)
    assert cenario.repo.impactos[(7, 1)]["situacao"] == "decisao_manual"


async def test_falha_ao_gravar_impacto_nao_impede_a_suspensao():
    cenario = montar_cenario()

    async def quebrado(*a, **k):
        raise RuntimeError("banco fora do ar")

    cenario.repo.gravar_impactos = quebrado
    await _incidente(cenario)
    assert cenario.erp.status(1) == "suspensa_preventiva" and cenario.canal.aprovacoes


# ------------------------------------------------------------------ cliente de clima
def test_delta_t_pela_formula_de_stull():
    assert delta_t(20, 50) == pytest.approx(6.3, abs=0.2)  # bulbo úmido de 13,7 °C (Stull, 2011)
    assert delta_t(30, 30) > 10 and delta_t(22, 95) < 1


RESPOSTA_OPEN_METEO = {
    "timezone": "America/Sao_Paulo",
    "hourly": {
        "time": ["2026-11-03T08:00", "2026-11-03T09:00"],
        "precipitation": [0.2, None],
        "wind_speed_10m": [7.5, 8.0],
        "relative_humidity_2m": [60, 58],
        "temperature_2m": [25.0, 26.0],
        "wind_direction_10m": [120, 130],
        "wind_gusts_10m": [15.0, 16.0],
        "temperature_80m": [25.4, None],
    },
}


def test_hora_sem_dado_basico_e_descartada_e_o_delta_t_e_calculado():
    [hora] = interpretar(RESPOSTA_OPEN_METEO)
    assert hora.ts == AGORA and hora.chuva_mm == 0.2
    assert (hora.vento_kmh, hora.rajada_kmh) == (5.6, 11.2)  # 7,5 e 15 km/h a 10 m, levados para 2 m
    assert hora.vento_dir_graus == 120 and hora.delta_t_c == delta_t(25.0, 60)
    assert hora.inversao is False  # 80 m só 0,4 °C mais quente: abaixo da margem de 0,5 °C


def test_inversao_termica_pede_ar_mais_quente_em_cima_e_vento_quase_parado():
    from agents.clientes.clima import inversao_termica

    assert inversao_termica(20.2, 22.6, 3.0) is True  # madrugada calma: o solo esfria antes do ar de cima
    assert inversao_termica(20.2, 22.6, 10.2) is False  # mesma diferença com vento: a camada se mistura
    assert inversao_termica(22.0, 22.3, 2.0) is False  # 0,3 °C é ruído do modelo
    assert inversao_termica(28.6, 26.5, 1.0) is False  # tarde: o ar esfria com a altura
    assert inversao_termica(25.0, None, 1.0) is None


@respx.mock
async def test_cliente_divide_a_consulta_de_talhoes_vizinhos_e_reporta_falha():
    rota = respx.get("https://clima.teste/v1/forecast").mock(
        return_value=httpx.Response(200, json=RESPOSTA_OPEN_METEO)
    )
    clima = OpenMeteoClima("https://clima.teste/v1/forecast")
    a = await clima.previsao(-17.7901, -50.9204, dias=7)
    b = await clima.previsao(-17.7949, -50.9151, dias=7)  # mesmo ponto com 2 casas decimais
    assert a is b and rota.call_count == 1
    assert rota.calls[0].request.url.params["past_days"] == "0"

    rota.mock(return_value=httpx.Response(429))
    with pytest.raises(ClimaIndisponivel):
        await clima.previsao(-10.0, -50.0)
    await clima.fechar()
