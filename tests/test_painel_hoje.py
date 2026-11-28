"""Tela Hoje e tela do incidente (conceito 2 do Figma): as funções puras de `painel.hoje` e as rotas."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from painel import dados, hoje, main

FUSO = dados.FUSO
AGORA = datetime(2026, 11, 24, 6, 12, tzinfo=FUSO)


def ts(dia, hora):
    return (AGORA + timedelta(days=dia)).replace(hour=hora, minute=0)


@pytest.fixture
def cliente():
    return TestClient(main.app)


def test_quando_e_extenso():
    assert hoje.quando(ts(0, 7), AGORA) == "hoje 07h"
    assert hoje.quando(ts(1, 6), AGORA) == "amanhã 06h"
    assert hoje.quando(ts(3, 9), AGORA) == "sex 27/11, 09h"
    assert hoje.quando_frase(ts(1, 6), AGORA) == "amanhã às 06h"
    assert hoje.quando_frase(ts(3, 9), AGORA) == "sex 27/11 às 09h"
    assert hoje.por_extenso(2, maiuscula=True) == "Duas" and hoje.por_extenso(1, feminino=False) == "um"
    assert hoje.por_extenso(12) == "12"
    assert hoje.reais_curto(4800.4) == "R$ 4.800" and hoje._num(12.0) == "12" and hoje._num(4.75) == "4,8"


def _plano(custos=True):
    return {
        "id": 7,
        "incidente_id": 12,
        "escalado_em": None,
        "mensagem": "texto do canal",
        "opcoes": [
            {"os_id": 44, "novo_inicio": ts(1, 6).isoformat(), "maquina_id": 2, "justificativa": "livre"}
        ],
        "custos_atraso": [
            {
                "os_id": 44,
                "vence_em": ts(0, 11).isoformat(),
                "custo_perder_janela_rs": 4800.0,
                "fonte_fator": "estimativa: 5%/dia",
                "proxima_janela": ts(2, 16).isoformat(),
            }
        ]
        if custos
        else [],
    }


ORDEM = {
    44: {
        "id": 44,
        "talhao": "Talhão 3",
        "talhao_id": 3,
        "produto": "Abacus HC",
        "inicio_previsto": ts(0, 10),
        "maquina": "Pulv-01",
        "area_ha": 42,
        "cultura": "soja",
    }
}


def test_plano_mostra_titulo_prazo_e_os_dois_custos():
    p = hoje.montar_plano(
        _plano(), ORDEM, {2: "Pulv-02"}, {44: {"perda_rs": 9600.0, "fonte_fator": "Embrapa"}}, AGORA
    )
    assert p["titulo"] == "Remarcar o Talhão 3 para amanhã às 06h, no Pulv-02"
    assert p["resumo"] == "Plano INC-0012 para a aplicação de Abacus HC marcada para hoje às 10h."
    assert p["prazo"] == "Decida até 11h" and not p["vencido"]
    item = p["itens"][0]
    assert item["custo_janela"] == "R$ 4.800" and item["estimativa"] and item["proxima"] == "qui 26/11, 16h"
    assert item["perda"] == "R$ 9.600" and item["fonte_perda"] == "Embrapa"


def test_plano_vencido_e_sem_custo():
    p = hoje.montar_plano(_plano(), ORDEM, {}, {}, ts(0, 12))
    assert p["vencido"] and p["prazo"] == "Janela vencida: peça outra opção"
    sem = hoje.montar_plano(_plano(custos=False), ORDEM, {}, {}, AGORA)
    assert (
        sem["prazo"] is None
        and sem["itens"][0]["custo_janela"] is None
        and sem["itens"][0]["maquina"] == "Pulv-01"
    )


def _os(status="agendada", hora=7):
    return {"id": 1, "status": status, "inicio_previsto": ts(0, hora), "duracao_horas": 4, "talhao_id": 1}


DETALHE = {
    "parametro": "delta_t",
    "rotulo": "Delta T",
    "valor": 9.2,
    "limite": 8,
    "unidade": "°C",
    "sentido": "max",
    "nivel": "amarelo",
    "inicio": ts(0, 13).isoformat(),
    "fim": ts(0, 15).isoformat(),
}


def test_estado_da_os_pela_bula_e_pela_previsao():
    previsao = [{"ts": ts(0, 7).isoformat(), "vento_kmh": 7, "delta_t_c": 4.8}]
    livre = hoje.estado_da_os(_os(), {"nivel": "verde", "detalhes": []}, previsao, True)
    assert (
        livre["selo"] == hoje.PODE_APLICAR
        and livre["motivo"] == "Vento de 7 km/h e Delta T 4,8 às 07h, dentro dos limites."
    )
    atencao = hoje.estado_da_os(_os(hora=14), {"nivel": "amarelo", "detalhes": [DETALHE]}, previsao, True)
    assert (
        atencao["selo"] == hoje.ATENCAO and atencao["motivo"] == "Delta T de 9,2 °C às 13h; o limite é 8 °C."
    )
    fora_do_horario = hoje.estado_da_os(
        _os(hora=7), {"nivel": "amarelo", "detalhes": [DETALHE]}, previsao, True
    )
    assert fora_do_horario["selo"] == hoje.PODE_APLICAR  # o limite passa só à tarde
    assert hoje.estado_da_os(_os("suspensa_preventiva"), None, None, False)["selo"] == hoje.SUSPENSA
    sem_monitor = hoje.estado_da_os(_os(), None, None, False)
    assert sem_monitor["selo"] is None and "Sem previsão" in sem_monitor["motivo"]


def test_regua_de_36h_posiciona_chuva_limite_e_horario():
    inicio = hoje.inicio_regua(AGORA)
    previsao = [
        {"ts": ts(0, 12).isoformat(), "chuva_mm": 3.0},
        {"ts": ts(0, 13).isoformat(), "chuva_mm": 0.1},
    ]
    r = hoje.regua(inicio, 36, previsao, [DETALHE], ts(0, 15), "atencao")
    assert r["divisor"] == 50.0  # 06h → 00h são 18 das 36 horas
    assert [c["hora"] for c in r["chuva"]] == ["12h"]  # garoa abaixo de 0,2 mm não pinta
    assert r["foras"][0]["esq"] == round(100 * 7 / 36, 2) and r["foras"][0]["tom"] == "atencao"
    assert r["marca"] == {"esq": round(100 * 9 / 36, 2), "tom": "atencao"}
    assert hoje.regua(inicio, 36, None, [], ts(2, 10), "bom")["marca"] is None  # fora da régua


def test_resumo_do_dia():
    plano = hoje.montar_plano(_plano(), ORDEM, {}, {}, AGORA)
    linhas = [
        {"selo": hoje.PODE_APLICAR},
        {"selo": hoje.PODE_APLICAR},
        {"selo": hoje.ATENCAO},
        {"selo": hoje.SUSPENSA},
    ]
    titulo, sub = hoje.resumo_do_dia([plano], linhas, AGORA)
    assert titulo == "Uma decisão espera você antes das 11h."
    assert sub == (
        "Quatro aplicações até amanhã: duas liberadas, uma pede outro horário e uma está suspensa pelo clima."
    )
    assert hoje.resumo_do_dia([], [], AGORA) == (
        "Nada espera a sua decisão agora.",
        "Nenhuma aplicação marcada até amanhã.",
    )


def test_mini_mapa_projeta_poligonos_e_pontos():
    quadrado = {
        "type": "Polygon",
        "coordinates": [[[-47.7, -15.6], [-47.69, -15.6], [-47.69, -15.61], [-47.7, -15.61], [-47.7, -15.6]]],
    }
    talhoes = [
        {"id": 1, "nome": "Talhão 1 - Sede", "lat": -15.6, "lon": -47.7, "geom": quadrado},
        {"id": 2, "nome": "Baixada", "lat": -15.62, "lon": -47.68, "geom": None},
    ]
    m = hoje.mini_mapa(talhoes, {1: {"nivel": "vermelho"}})
    assert [f["tom"] for f in m["formas"]] == ["risco", "neutro"]
    assert m["formas"][0]["caminho"].startswith("M") and m["formas"][1]["caminho"] is None
    assert [f["rotulo"] for f in m["formas"]] == ["1", "Ba"]
    assert all(0 <= f["x"] <= 300 and 0 <= f["y"] <= 170 for f in m["formas"])
    assert hoje.contagem_risco(talhoes, {1: {"nivel": "vermelho"}}) == {
        "risco": 1,
        "atencao": 0,
        "bom": 0,
        "neutro": 1,
    }
    assert hoje.mini_mapa([], {})["formas"] == []


def test_incidente_inexistente_e_404(cliente):
    assert cliente.get("/incidente/999999").status_code == 404


def test_hoje_monta_sem_monitor(cliente, monkeypatch):
    """Monitor fora do ar: a Hoje abre, sem selo de previsão e com o aviso."""
    monkeypatch.setattr(main, "_risco_do_monitor", lambda *_: None)
    monkeypatch.setattr(main, "_previsoes", lambda *_: {})
    r = cliente.get("/visao-geral")
    assert r.status_code == 200 and "Precisa de você" in r.text and "Talhões nas próximas 48 h" in r.text


# --- Desempenho: a Hoje e o Mapa não esperam o Monitor --------------------------------------------
def _cliente_contado(monkeypatch, handler):
    """Troca o cliente HTTP do painel por um simulado; devolve a lista de caminhos pedidos ao Monitor."""
    import httpx

    vistos = []

    def transporte(req):
        vistos.append(req.url.path)
        return handler(req)

    monkeypatch.setattr(main, "cliente_http", lambda: httpx.Client(transport=httpx.MockTransport(transporte)))
    return vistos


def test_monitor_fora_do_ar_pausa_as_tentativas(monkeypatch):
    import httpx

    def recusa(req):
        raise httpx.ConnectError("recusada", request=req)

    vistos = _cliente_contado(monkeypatch, recusa)
    assert main._risco_do_monitor(None) is None
    assert main._risco_do_monitor(None) is None and main._previsoes({1, 2}) == {1: None, 2: None}
    assert vistos == ["/risco"]  # depois da primeira recusa, nada vai ao Monitor durante a pausa
    main.limpar_cache_monitor()
    main._risco_do_monitor(None)
    assert vistos == ["/risco", "/risco"]


def test_risco_fica_em_cache(monkeypatch):
    import httpx

    vistos = _cliente_contado(
        monkeypatch, lambda req: httpx.Response(200, json=[{"talhao_id": 1, "nivel": "verde"}])
    )
    assert (
        main._risco_do_monitor(None) == main._risco_do_monitor(None) == [{"talhao_id": 1, "nivel": "verde"}]
    )
    assert vistos == ["/risco"]
    assert main._risco_em_cache(None) == {1: {"talhao_id": 1, "nivel": "verde"}}


def test_hoje_e_mapa_abrem_sem_chamar_o_monitor(cliente, monkeypatch):
    vistos = _cliente_contado(
        monkeypatch, lambda req: (_ for _ in ()).throw(AssertionError("chamou o Monitor"))
    )
    hoje_html = cliente.get("/visao-geral").text
    assert 'hx-get="/parcial/hoje"' in hoje_html and 'id="talhoes-conteudo"' in hoje_html
    assert cliente.get("/mapa").status_code == 200
    assert vistos == []


def test_parcial_hoje_traz_subtitulo_e_talhoes_fora_de_banda(cliente, monkeypatch):
    monkeypatch.setattr(main, "_risco_do_monitor", lambda *_: None)
    html = cliente.get("/parcial/hoje").text
    assert '<span id="subtitulo-dia" hx-swap-oob="innerHTML">' in html
    assert '<div id="talhoes-conteudo" hx-swap-oob="innerHTML">' in html
    assert "O Monitor não respondeu" in html


def test_api_risco_traz_o_motivo_da_etiqueta(cliente, monkeypatch):
    detalhe = {
        "parametro": "vento",
        "rotulo": "Vento",
        "valor": 14,
        "limite": 11,
        "unidade": "km/h",
        "sentido": "max",
        "nivel": "amarelo",
        "inicio": "2026-11-24T14:00:00-03:00",
        "fim": "2026-11-24T18:00:00-03:00",
    }
    monkeypatch.setattr(
        main, "_risco_do_monitor", lambda *_: [{"talhao_id": 1, "nivel": "amarelo", "detalhes": [detalhe]}]
    )
    corpo = cliente.get("/api/risco").json()
    assert (
        corpo[0]["motivo"] == "Vento de 14 km/h às 14h; o limite é 11 km/h."
        and corpo[0]["nivel"] == "amarelo"
    )
