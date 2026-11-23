from datetime import timedelta

from monitor.fontes import ReplayFonte
from monitor.main import CENARIOS
from monitor.modelos import HoraPrevisao, Limiares
from monitor.risco import classificar
from monitor.tests.conftest import AGORA

LIMIARES = Limiares()


def horas(n=48, **campos):
    base = dict(chuva_mm=0, vento_kmh=5, umidade_pct=70, temp_c=25)
    base.update(campos)
    return [HoraPrevisao(ts=AGORA + timedelta(hours=i), **base) for i in range(n)]


def test_chuva_80mm_fica_vermelho():
    previsao = ReplayFonte(CENARIOS / "chuva_80mm.json").obter(-21.0, -47.0, AGORA)
    risco = classificar(1, previsao.horas, LIMIARES, AGORA)
    assert risco.nivel == "vermelho" and risco.talhao_id == 1
    assert any("chuva" in m for m in risco.motivos) and risco.ate is not None


def test_vermelho_motivo_com_limite_e_horario_de_sao_paulo():
    hs = horas()
    hs[10] = hs[10].model_copy(update={"chuva_mm": 30})
    risco = classificar(1, hs, LIMIARES, AGORA)
    assert risco.motivos == ["chuva 30 mm até 30/10 18h; limite 20 mm"]
    assert risco.ate == hs[10].ts


def test_vermelho_inversao():
    hs = horas()
    hs[3] = hs[3].model_copy(update={"inversao": True})
    risco = classificar(1, hs, LIMIARES, AGORA)
    assert risco.motivos == ["inversão térmica prevista até 30/10 11h"]


def test_quase_no_limite_fica_amarelo():
    hs = horas()
    hs[5] = hs[5].model_copy(update={"chuva_mm": 8})
    hs[6] = hs[6].model_copy(update={"chuva_mm": 8})
    risco = classificar(1, hs, LIMIARES, AGORA)
    assert risco.nivel == "amarelo"
    assert risco.motivos == ["chuva 16 mm em 48 h (80% do limite de 20 mm)"]
    assert risco.ate == hs[6].ts


def test_amarelo_por_vento_umidade_e_delta_t():
    hs = horas(vento_kmh=9, umidade_pct=58, delta_t_c=1.5)  # vento a 2 m: limite 11, atenção a partir de 8,25
    risco = classificar(1, hs, LIMIARES, AGORA)
    assert risco.nivel == "amarelo"
    assert len(risco.motivos) == 3
    assert risco.ate == hs[-1].ts


def test_amarelo_por_rajada_ignora_none():
    hs = horas()
    hs[2] = hs[2].model_copy(update={"rajada_kmh": 12})  # rajada a 2 m: limite 15, atenção a partir de 11,25
    risco = classificar(1, hs, LIMIARES, AGORA)
    assert risco.nivel == "amarelo" and "rajada" in risco.motivos[0]


def test_vento_calmo_nao_e_amarelo():
    assert classificar(1, horas(vento_kmh=0.5), LIMIARES, AGORA).nivel == "verde"


def test_seco_fica_verde():
    risco = classificar(1, horas(), LIMIARES, AGORA)
    assert risco.nivel == "verde" and risco.motivos == [] and risco.ate is None


def test_horizonte_ignora_janela_dos_limiares():
    hs = horas(60)
    hs[55] = hs[55].model_copy(update={"chuva_mm": 30})
    assert classificar(1, hs, Limiares(janela_h=72), AGORA).nivel == "verde"


def test_detalhe_do_vermelho_traz_valor_limite_e_periodo():
    hs = horas()
    hs[10] = hs[10].model_copy(update={"rajada_kmh": 22.9})
    risco = classificar(1, hs, LIMIARES, AGORA)
    assert risco.motivos == ["rajada 22,9 km/h até 30/10 18h; limite 15 km/h"]
    [d] = risco.detalhes
    assert (d.parametro, d.rotulo, d.nivel, d.valor, d.limite, d.unidade, d.sentido) == (
        "rajada", "Rajada", "vermelho", 22.9, 15.0, "km/h", "max")
    assert d.inicio == hs[10].ts and d.fim == hs[10].ts


def test_detalhe_da_inversao_nao_tem_valor():
    hs = horas()
    hs[3] = hs[3].model_copy(update={"inversao": True})
    [d] = classificar(1, hs, LIMIARES, AGORA).detalhes
    assert d.parametro == "inversao" and d.sentido == "evento" and d.valor is None and d.limite is None


def test_detalhe_do_amarelo_e_numero_com_virgula():
    hs = horas()
    hs[2] = hs[2].model_copy(update={"rajada_kmh": 12.4})
    risco = classificar(1, hs, LIMIARES, AGORA)
    assert risco.motivos == ["rajada até 12,4 km/h (perto do limite de 15 km/h)"]
    [d] = risco.detalhes
    assert d.nivel == "amarelo" and d.valor == 12.4 and d.inicio == d.fim == hs[2].ts


def test_verde_sem_detalhes():
    assert classificar(1, horas(), LIMIARES, AGORA).detalhes == []
