from datetime import timedelta

import pytest

from monitor.avaliacao import avaliar
from monitor.modelos import HoraPrevisao, Limiares
from monitor.tests.conftest import AGORA

LIMIARES = Limiares(chuva_mm=20, vento_kmh=15, umidade_min_pct=50, janela_h=48)


def horas(n=60, chuva=None, vento=None, umidade=None):
    chuva, vento, umidade = chuva or {}, vento or {}, umidade or {}
    return [HoraPrevisao(ts=AGORA + timedelta(hours=i), chuva_mm=chuva.get(i, 0), vento_kmh=vento.get(i, 5),
                         umidade_pct=umidade.get(i, 70), temp_c=25) for i in range(n)]


def chuva_em(total, em_horas, comecando_em_h=10):
    return {comecando_em_h + i: total / em_horas for i in range(em_horas)}


def test_chuva_80mm_dispara_com_inicio_e_fim():
    [g] = avaliar(horas(chuva=chuva_em(80, 20, 14)), LIMIARES, AGORA)
    assert g.tipo == "chuva" and g.valor == pytest.approx(80)
    assert (g.inicio, g.fim) == (AGORA + timedelta(hours=14), AGORA + timedelta(hours=33))


def test_abaixo_do_limiar_nao_dispara():
    assert avaliar(horas(chuva=chuva_em(19.9, 12)), LIMIARES, AGORA) == []


def test_chuva_fora_da_janela_ignorada():
    assert avaliar(horas(chuva=chuva_em(80, 6, comecando_em_h=50)), LIMIARES, AGORA) == []


def test_horas_passadas_ignoradas():
    previsao = horas(chuva={0: 80})
    assert avaliar(previsao, LIMIARES, AGORA + timedelta(hours=1)) == []


def test_vento_e_umidade():
    tipos = {g.tipo for g in avaliar(horas(vento={5: 22}, umidade={30: 40}), LIMIARES, AGORA)}
    assert tipos == {"vento", "umidade"}


def test_sem_horas_nao_dispara():
    assert avaliar([], LIMIARES, AGORA) == []


def test_limiares_trazem_padroes_da_tabela_de_condicoes():
    """E7: padrões saídos de docs/pesquisa/condicoes-aplicacao.md. Vento e rajada a 2 m (G1): 11 e 15 km/h
    equivalem aos 15 e 20 km/h a 10 m de antes."""
    padrao = Limiares()
    assert (padrao.chuva_mm, padrao.vento_kmh, padrao.umidade_min_pct, padrao.janela_h) == (20, 11, 50, 48)
    assert padrao.rajada_kmh == 15
    assert (padrao.delta_t_min_c, padrao.delta_t_max_c, padrao.inversao_vento_max_kmh) == (2, 8, 2)


def com_campos(**por_hora):
    """horas() com campos opcionais por hora: com_campos(rajada_kmh={5: 25})."""
    previsao = horas()
    for campo, valores in por_hora.items():
        for i, v in valores.items():
            setattr(previsao[i], campo, v)
    return previsao


def test_gatilho_rajada():
    [g] = avaliar(com_campos(rajada_kmh={5: 15, 6: 31, 7: 12}), LIMIARES, AGORA)
    assert g.tipo == "rajada" and g.valor == 31 and g.limiar == 15
    assert (g.inicio, g.fim) == (AGORA + timedelta(hours=5), AGORA + timedelta(hours=6))


def test_gatilho_delta_t_alto():
    [g] = avaliar(com_campos(delta_t_c={3: 9, 4: 11, 5: 8}), LIMIARES, AGORA)
    assert g.tipo == "delta_t" and g.valor == 11 and g.limiar == 8
    assert (g.inicio, g.fim) == (AGORA + timedelta(hours=3), AGORA + timedelta(hours=4))


def test_delta_t_baixo_nao_dispara():
    assert avaliar(com_campos(delta_t_c={i: 0.5 for i in range(10)}), LIMIARES, AGORA) == []


def test_gatilho_inversao_valor_1_limiar_0():
    [g] = avaliar(com_campos(inversao={2: True, 3: True, 4: False}), LIMIARES, AGORA)
    assert (g.tipo, g.valor, g.limiar) == ("inversao", 1, 0)
    assert (g.inicio, g.fim) == (AGORA + timedelta(hours=2), AGORA + timedelta(hours=3))


def test_campos_none_nao_disparam():
    tipos = {g.tipo for g in avaliar(horas(vento={5: 22}, umidade={30: 40}), LIMIARES, AGORA)}
    assert tipos == {"vento", "umidade"}


def test_do_ambiente_le_limiares_novos(monkeypatch):
    for env, v in {"LIMIAR_RAJADA_KMH": "25", "LIMIAR_DELTA_T_MAX_C": "9", "LIMIAR_DELTA_T_MIN_C": "1.5",
                   "LIMIAR_INVERSAO_VENTO_MAX_KMH": "3"}.items():
        monkeypatch.setenv(env, v)
    l = Limiares.do_ambiente()
    assert (l.rajada_kmh, l.delta_t_max_c, l.delta_t_min_c) == (25, 9, 1.5)
    assert l.inversao_vento_max_kmh == 3
