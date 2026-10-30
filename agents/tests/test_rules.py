from datetime import timedelta

import pytest
from pydantic import ValidationError

from agents.modelos import Impacto, OsEmRisco, PlanoProposto, ViolacaoOs
from agents.rules.impacto import calcular_impacto, calcular_perda, formatar_reais
from agents.rules.janelas import janelas_climaticas, motivo_invalida
from agents.rules.mensagem import compor_mensagem
from agents.rules.previsao import resumir
from agents.rules.validador import ContextoPlano, janelas_validas, validar
from tests.fakes import AGORA, FakeConhecimento, montar_cenario, os_ureia, previsao_chuva

CHUVA_30_24H = ViolacaoOs(
    os_id=1,
    viola=True,
    parametro="chuva",
    limite=30,
    janela_h=24,
    trecho="...",
    fonte_doc="embrapa-ureia",
    pagina=4,
    justificativa="x",
)
JANELA_1 = AGORA.replace(hour=7) + timedelta(days=1)


def os_em_risco(**extra) -> OsEmRisco:
    dados = {**os_ureia(), **extra}
    return OsEmRisco(os_id=dados.pop("id"), talhao="Talhão 1", area_ha=120, **dados)


# ---------------------------------------------------------------- modelos


def test_violacao_exige_citacao():
    with pytest.raises(ValidationError, match="fonte_doc"):
        ViolacaoOs(os_id=1, viola=True, parametro="chuva", limite=30, trecho="x", pagina=1, justificativa="x")


def test_violacao_rejeita_unidade_trocada():
    with pytest.raises(ValidationError, match="fora da faixa"):
        ViolacaoOs.model_validate(CHUVA_30_24H.model_dump() | {"parametro": "vento", "limite": 900})


def test_sem_violacao_dispensa_citacao():
    assert ViolacaoOs(os_id=1, viola=False, justificativa="sem limite no documento").parametro is None


# ---------------------------------------------------------------- impacto


def test_perda_da_demo():
    assert calcular_perda(120, 200, 3.2, 0.3) == 23040.00
    assert formatar_reais(23040.0) == "R$ 23.040,00"
    assert formatar_reais(None) == "não calculada"


async def test_impacto_sem_fator_nao_inventa_numero():
    impacto = await calcular_impacto(os_em_risco(), "chuva", FakeConhecimento())
    assert impacto.perda_rs is None and impacto.fonte_fator == "sem fator cadastrado"


async def test_impacto_com_fator_citado():
    conhecimento = FakeConhecimento(
        fatores={("fertilizante", "chuva"): {"fator": 0.3, "fonte_ou_estimativa": "x:p.4"}}
    )
    impacto = await calcular_impacto(os_em_risco(), "chuva", conhecimento)
    assert impacto.perda_rs == 23040.00 and impacto.fonte_fator == "x:p.4"


# ---------------------------------------------------------------- janelas


def test_primeira_janela_depois_da_chuva():
    janelas = janelas_climaticas(previsao_chuva(), CHUVA_30_24H, 8, AGORA)
    assert janelas[0] == JANELA_1


def test_janela_na_chuva_e_invalida():
    motivo = motivo_invalida(AGORA + timedelta(hours=4), previsao_chuva(), CHUVA_30_24H, 8, AGORA)
    assert "acima do limite" in motivo


def test_fora_do_horario_e_invalida():
    noite = AGORA.replace(hour=22) + timedelta(days=2)
    assert "horário de trabalho" in motivo_invalida(noite, previsao_chuva(), CHUVA_30_24H, 8, AGORA)


def test_snapshot_curto_nao_extrapola():
    curto = previsao_chuva(horas=30)
    assert janelas_climaticas(curto, CHUVA_30_24H, 8, AGORA) == []


def test_exclui_datas_ja_oferecidas():
    janelas = janelas_climaticas(previsao_chuva(), CHUVA_30_24H, 8, AGORA, excluir={JANELA_1})
    assert JANELA_1 not in janelas and janelas[0] == JANELA_1 + timedelta(hours=1)


def test_vento_e_umidade():
    horas = previsao_chuva(total_mm=0)
    vento = CHUVA_30_24H.model_copy(update={"parametro": "vento", "limite": 5, "janela_h": None})
    assert "vento" in motivo_invalida(JANELA_1, horas, vento, 8, AGORA)
    umidade = CHUVA_30_24H.model_copy(update={"parametro": "umidade", "limite": 80, "janela_h": None})
    assert "umidade" in motivo_invalida(JANELA_1, horas, umidade, 8, AGORA)


# ---------------------------------------------------------------- validador


def contexto_da_demo() -> ContextoPlano:
    return ContextoPlano(
        horas=previsao_chuva(),
        os_por_id={1: os_em_risco()},
        violacoes_por_os={1: [CHUVA_30_24H]},
        os_suspensas=[1],
    )


def plano(novo_inicio, maquina_id=1, os_id=1) -> PlanoProposto:
    return PlanoProposto(
        opcoes=[{"os_id": os_id, "novo_inicio": novo_inicio, "maquina_id": maquina_id, "justificativa": "x"}],
        mensagem="m",
    )


async def erp_com_os_suspensa():
    c = montar_cenario()
    c.erp.os[1]["status"] = "suspensa_preventiva"
    return c.erp


async def test_validador_aprova_janela_valida():
    veredito = await validar(plano(JANELA_1), contexto_da_demo(), await erp_com_os_suspensa(), AGORA)
    assert veredito.aprovado, veredito.motivos


async def test_validador_reprova_maquina_ocupada():
    erp = await erp_com_os_suspensa()
    erp.os[3] = os_ureia(id=3, inicio_previsto=JANELA_1, maquina_id=1)
    veredito = await validar(plano(JANELA_1), contexto_da_demo(), erp, AGORA)
    assert not veredito.aprovado and "máquina 1 ocupada" in veredito.motivos[0]


async def test_validador_reprova_os_fora_do_incidente_e_falta_de_opcao():
    veredito = await validar(plano(JANELA_1, os_id=2), contexto_da_demo(), await erp_com_os_suspensa(), AGORA)
    assert not veredito.aprovado
    assert any("faltou opção" in m for m in veredito.motivos)
    assert any("não está suspensa" in m for m in veredito.motivos)


async def test_janelas_validas_com_maquina():
    janelas = await janelas_validas(contexto_da_demo(), 1, await erp_com_os_suspensa(), AGORA)
    assert janelas[0] == {"inicio": JANELA_1.isoformat(), "maquinas_livres": [1, 2]}
    assert len(janelas) == 3


# ---------------------------------------------------------------- previsão e mensagem


def test_resumo_da_previsao():
    blocos = resumir(previsao_chuva(), AGORA)
    assert len(blocos) == 20 and blocos[0]["chuva_mm"] == pytest.approx(13.3, abs=0.1)
    assert sum(b["chuva_mm"] for b in blocos[:6]) == pytest.approx(80, abs=0.5)  # blocos arredondados


def test_mensagem_tem_rodape_conferido():
    impacto = Impacto(
        os_id=1,
        area_ha=120,
        dose_por_ha=200,
        preco_unitario=3.2,
        fator=0.3,
        fonte_fator="x:p.4",
        perda_rs=23040,
    )
    texto = compor_mensagem(plano(JANELA_1), {1: os_em_risco()}, {1: [CHUVA_30_24H]}, {1: impacto})
    assert (
        "Perda evitada: R$ 23.040,00" in texto
        and "embrapa-ureia, p. 4" in texto
        and "04/11 às 07:00" in texto
    )


# ---------------------------------------------------------------- achados da revisão


def test_chuva_depois_do_fim_da_aplicacao_conta():
    """'Não chover 24 h após a aplicação' numa OS de 8 h vale até 32 h depois do início."""
    horas = previsao_chuva(total_mm=0)
    for h in horas:
        if h.ts == JANELA_1 + timedelta(hours=28):
            h.chuva_mm = 40
    motivo = motivo_invalida(JANELA_1, horas, CHUVA_30_24H, 8, AGORA)
    assert motivo and "chuva prevista de 40.0 mm" in motivo


def test_os_com_duas_violacoes_respeita_as_duas():
    vento = CHUVA_30_24H.model_copy(update={"parametro": "vento", "limite": 5, "janela_h": None})
    assert motivo_invalida(JANELA_1, previsao_chuva(), CHUVA_30_24H, 8, AGORA) is None
    assert "vento" in motivo_invalida(JANELA_1, previsao_chuva(), [CHUVA_30_24H, vento], 8, AGORA)


async def test_validador_reprova_mesma_maquina_para_duas_os():
    contexto = contexto_da_demo()
    contexto.os_por_id[2] = os_em_risco(id=2)
    contexto.violacoes_por_os[2] = [CHUVA_30_24H.model_copy(update={"os_id": 2})]
    contexto.os_suspensas = [1, 2]
    erp = await erp_com_os_suspensa()
    duas = PlanoProposto(
        opcoes=[
            {"os_id": 1, "novo_inicio": JANELA_1, "maquina_id": 1, "justificativa": "x"},
            {"os_id": 2, "novo_inicio": JANELA_1 + timedelta(hours=2), "maquina_id": 1, "justificativa": "x"},
        ],
        mensagem="m",
    )
    veredito = await validar(duas, contexto, erp, AGORA)
    assert not veredito.aprovado and "já usada pela OS 1" in veredito.motivos[0]


async def test_maquina_de_outro_tipo_nao_conta():
    contexto = contexto_da_demo()
    contexto.os_por_id[1] = os_em_risco(tipo_maquina="pulverizador")
    erp = await erp_com_os_suspensa()  # as duas máquinas da fazenda são distribuidores
    assert await janelas_validas(contexto, 1, erp, AGORA) == []
    assert not (await validar(plano(JANELA_1), contexto, erp, AGORA)).aprovado


async def test_revalidacao_com_erp_fora_nao_invalida_a_janela():
    erp = await erp_com_os_suspensa()
    erp.indisponivel = True
    assert not (await validar(plano(JANELA_1), contexto_da_demo(), erp, AGORA)).aprovado
    assert (await validar(plano(JANELA_1), contexto_da_demo(), erp, AGORA, exigir_erp=False)).aprovado
