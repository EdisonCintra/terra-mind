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
