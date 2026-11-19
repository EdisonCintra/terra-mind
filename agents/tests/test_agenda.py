"""G2 e G3: agenda semanal e avaliação de janela, sobre os limites do produto e a previsão."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from agents.agenda import AgendaIndisponivel, NaoEncontrado, PedidoJanela, ServicoAgenda
from agents.api import Servicos, criar_api
from agents.modelos import HoraPrevisao, LimiteProduto
from agents.rules.agenda import agenda_do_talhao
from agents.rules.limites import GENERICOS, limites_efetivos
from agents.tests.fakes import AGORA, TRECHO_BULA, montar_cenario

CHUVA_BULA = LimiteProduto(
    parametro="chuva", sentido="maximo", limite=0, unidade="mm", janela_h=4,
    trecho=TRECHO_BULA, fonte_doc="bula-abacus-hc", pagina=7,
)  # fmt: skip
HOJE = AGORA.date()


def previsao(horas: int = 168, **por_hora) -> list[HoraPrevisao]:
    """Tempo bom o tempo todo; `por_hora` troca um campo por uma função do índice da hora."""
    base = {"chuva_mm": 0, "vento_kmh": 6, "umidade_pct": 70, "temp_c": 24, "delta_t_c": 4}
    return [
        HoraPrevisao(
            ts=AGORA + timedelta(hours=i), **{**base, **{campo: f(i) for campo, f in por_hora.items()}}
        )
        for i in range(horas)
    ]


def test_limite_da_bula_substitui_o_generico_do_mesmo_parametro():
    efetivos = limites_efetivos([CHUVA_BULA])
    chuva = [x for x in efetivos if x.parametro == "chuva"]
    assert chuva == [CHUVA_BULA]
    assert len(efetivos) == len(GENERICOS)  # os outros parâmetros seguem na referência genérica
    assert all(x.generico for x in efetivos if x.parametro != "chuva")


def test_limite_de_bula_sem_pagina_e_rejeitado():
    with pytest.raises(ValueError, match="sem trecho, fonte ou página"):
        LimiteProduto(parametro="vento", limite=10)


def test_agenda_marca_as_horas_de_chuva_como_ruins_com_a_pagina_da_bula():
    # Chove das 12h às 15h de hoje (AGORA = 08h): com 2 h de aplicação e 4 h sem chuva depois,
    # nenhum início de hoje escapa; amanhã o dia inteiro serve.
    horas = previsao(chuva_mm=lambda i: 5 if 4 <= i < 8 else 0)
    hoje, amanha = agenda_do_talhao(horas, limites_efetivos([CHUVA_BULA]), 2, AGORA, HOJE, 2)

    assert hoje.data == HOJE and hoje.horas_boas == 3  # 16h, 17h e 18h: a chuva acaba às 16h
    ruins = [f for f in hoje.faixas if not f.serve and f.parametro == "chuva:maximo"]
    assert ruins and ruins[0].pagina == 7 and ruins[0].fonte == "bula, p. 7"
    assert "chuva prevista" in ruins[0].motivo
    assert amanha.horas_boas == 13 and [f.serve for f in amanha.faixas] == [True]  # 06h às 18h


def test_garoa_do_modelo_nao_reprova_a_bula_que_pede_sem_chuva():
    # 0,1 mm por hora: 0,6 mm nas 6 h de uma aplicação de 2 h + 4 h. Traço, não chuva.
    garoa = previsao(chuva_mm=lambda i: 0.1)
    hoje = agenda_do_talhao(garoa, [CHUVA_BULA], 2, AGORA, HOJE, 1)[0]
    assert hoje.horas_boas == 9  # 10h às 18h
    # 0,3 mm por hora já soma 1,8 mm: passa do traço e vale o limite da bula.
    chuvisco = previsao(chuva_mm=lambda i: 0.3)
    assert agenda_do_talhao(chuvisco, [CHUVA_BULA], 2, AGORA, HOJE, 1)[0].horas_boas == 0


def test_agenda_agrupa_horas_seguidas_e_marca_o_que_ja_passou():
    hoje = agenda_do_talhao(
        previsao(vento_kmh=lambda i: 20 if 4 <= i < 6 else 6), list(GENERICOS), 1, AGORA, HOJE, 1
    )[0]
    # 08h e 09h já passaram (antecedência de 2 h), 10h e 11h boas, 12h e 13h com vento, 14h–18h boas.
    resumo = [(f.inicio.hour, f.fim.hour, f.serve, f.parametro) for f in hoje.faixas]
    assert resumo == [
        (8, 10, False, "horario"),
        (10, 12, True, None),
        (12, 14, False, "vento:maximo"),
        (14, 19, True, None),
    ]
    assert hoje.faixas[2].fonte == "referência genérica" and hoje.faixas[2].pagina is None


def test_agenda_nao_mostra_hora_que_a_previsao_nao_alcanca():
    dias = agenda_do_talhao(previsao(horas=30), list(GENERICOS), 8, AGORA, HOJE, 3)
    ultima = dias[-1].faixas[-1]
    assert ultima.fim <= AGORA + timedelta(hours=30 - 8 - 2 + 1)  # 8 h de aplicação + 2 h de chuva


async def test_semana_usa_os_limites_do_produto_e_a_previsao_do_talhao():
    c = montar_cenario()
    c.repo.limites["Abacus HC"] = [CHUVA_BULA]
    agenda = await ServicoAgenda(c.deps).semana(insumo_id=1, dias=3)

    assert agenda.produto == "Abacus HC" and agenda.bula_lida
    assert c.clima.pedidos[0][:2] == (-17.79, -50.92)
    [talhao] = agenda.talhoes
    # Cenário da demo: 80 mm nas primeiras 36 h. Hoje e amanhã não têm hora boa.
    assert [d.horas_boas for d in talhao.dias[:2]] == [0, 0]
    assert talhao.dias[2].horas_boas > 0


async def test_semana_sem_bula_lida_usa_a_referencia_generica_e_avisa():
    c = montar_cenario()
    agenda = await ServicoAgenda(c.deps).semana(insumo_id=1, dias=1)
    assert not agenda.bula_lida and all(x.generico for x in agenda.limites)


async def test_semana_com_previsao_fora_do_ar_e_insumo_inexistente():
    c = montar_cenario()
    with pytest.raises(NaoEncontrado):
        await ServicoAgenda(c.deps).semana(insumo_id=99)
    c.clima.indisponivel = True
    with pytest.raises(AgendaIndisponivel, match="previsão indisponível"):
        await ServicoAgenda(c.deps).semana(insumo_id=1)


async def test_janela_na_chuva_avisa_cita_a_bula_e_sugere_horario_com_maquina_livre():
    c = montar_cenario()
    c.repo.limites["Abacus HC"] = [CHUVA_BULA]
    pedido = PedidoJanela(talhao_id=1, insumo_id=1, inicio=AGORA + timedelta(hours=6), duracao_horas=4)
    r = await ServicoAgenda(c.deps).avaliar(pedido)

    assert not r.serve
    assert "terça, 03/11 às 14:00" in r.motivo and "chuva prevista" in r.motivo and "bula, p. 7" in r.motivo
    assert r.citacao.pagina == 7 and r.citacao.trecho == TRECHO_BULA
    assert r.sugestoes and all(s.inicio > pedido.inicio for s in r.sugestoes)
    primeira = r.sugestoes[0]
    assert primeira.inicio == AGORA.replace(hour=6) + timedelta(days=2)  # 05/11 06:00, depois da chuva
    assert primeira.maquina_id == 1 and "quinta, 05/11 às 06:00" == primeira.quando


async def test_janela_boa_sem_maquina_livre_nao_serve_e_sugere_outra():
    c = montar_cenario()
    c.clima.horas = previsao()
    c.erp.maquinas[2]["status"] = "manutencao"
    inicio = AGORA + timedelta(hours=23)  # 07h de amanhã: a OS 1 ocupa o Pulverizador A das 04h às 12h
    r = await ServicoAgenda(c.deps).avaliar(
        PedidoJanela(talhao_id=1, insumo_id=1, inicio=inicio, duracao_horas=2)
    )
    assert not r.serve and "nenhum pulverizador livre" in r.motivo and r.citacao is None
    assert r.sugestoes and r.sugestoes[0].maquina_id == 1


async def test_janela_boa_com_maquina_livre_serve_sem_sugestoes():
    c = montar_cenario()
    c.clima.horas = previsao()
    pedido = PedidoJanela(talhao_id=1, insumo_id=1, inicio=AGORA + timedelta(hours=3), duracao_horas=2)
    r = await ServicoAgenda(c.deps).avaliar(pedido)
    assert r.serve and r.motivo is None and r.sugestoes == []


async def test_janela_fora_do_horario_de_trabalho():
    c = montar_cenario()
    c.clima.horas = previsao()
    pedido = PedidoJanela(talhao_id=1, insumo_id=1, inicio=AGORA.replace(hour=22), duracao_horas=2)
    r = await ServicoAgenda(c.deps).avaliar(pedido)
    assert not r.serve and "fora do horário de trabalho" in r.motivo and r.sugestoes


def _cliente(cenario):
    async def fabrica():
        class _Orq:
            deps = cenario.deps

            async def aguardar(self):
                return None

        return Servicos(orquestrador=_Orq(), agenda=ServicoAgenda(cenario.deps))

    return TestClient(criar_api(fabrica))


def test_rotas_do_contrato_24():
    c = montar_cenario()
    with _cliente(c) as http:
        r = http.get("/agenda", params={"insumo_id": 1, "dias": 2})
        assert r.status_code == 200 and r.json()["talhoes"][0]["talhao"] == "Talhão 1 - Sede"
        assert http.get("/agenda", params={"insumo_id": 99}).status_code == 404
        corpo = {
            "talhao_id": 1,
            "insumo_id": 1,
            "inicio": (AGORA + timedelta(hours=6)).isoformat(),
            "duracao_horas": 4,
        }
        r = http.post("/janelas/avaliar", json=corpo)
        assert r.status_code == 200
        assert set(r.json()) >= {"serve", "motivo", "citacao", "sugestoes"}
        assert r.json()["sugestoes"][0].keys() >= {"inicio", "maquina_id"}
        assert http.post("/limites/extrair", json={"produto": "Abacus HC"}).status_code == 503
