from datetime import datetime, timedelta

import respx

from monitor.fontes import OPEN_METEO, ReplayFonte
from monitor.gravar import gravar
from monitor.main import CENARIOS
from monitor.tests.conftest import AGORA, SP


def test_rebase_para_agora_truncado_na_hora():
    horas = ReplayFonte(CENARIOS / "chuva_80mm.json").obter(0, 0, datetime(2026, 11, 3, 14, 37, tzinfo=SP)).horas
    assert horas[0].ts == datetime(2026, 11, 3, 14, 0, tzinfo=SP)
    assert horas[1].ts - horas[0].ts == timedelta(hours=1)


def test_cenario_tem_120h_e_80mm_nas_primeiras_36h():
    horas = ReplayFonte(CENARIOS / "chuva_80mm.json").obter(0, 0, AGORA).horas
    assert len(horas) == 120
    assert sum(h.chuva_mm for h in horas[:36]) == 80


def test_cenario_chove_na_hora_da_os_do_seed_e_seca_depois():
    horas = ReplayFonte(CENARIOS / "chuva_80mm.json").obter(0, 0, AGORA).horas
    assert horas[20].chuva_mm > 0                       # OS do seed: now() + 20 h
    assert all(h.chuva_mm == 0 for h in horas[40:])     # janela seca para reagendar


@respx.mock
def test_gravar_salva_resposta_crua(tmp_path, payload_open_meteo):
    respx.get(OPEN_METEO).respond(200, json=payload_open_meteo)
    saida = tmp_path / "cenario.json"
    gravar(-17.79, -50.92, saida)
    assert ReplayFonte(saida).obter(0, 0, AGORA).horas[0].ts == AGORA


def test_replay_se_identifica_como_replay():
    assert ReplayFonte(CENARIOS / "chuva_80mm.json").nome == "replay"


def test_replay_antigo_continua_lendo():
    """Cenário sem os campos novos ainda gera o incidente (contrato 21: campos opcionais)."""
    previsao = ReplayFonte(CENARIOS / "chuva_80mm.json").obter(0, 0, AGORA)
    assert previsao.dias == []
    assert previsao.horas[0].inversao is None and previsao.horas[0].rajada_kmh is None
    from monitor.avaliacao import avaliar
    from monitor.modelos import Limiares
    assert avaliar(previsao.horas, Limiares(), AGORA)
