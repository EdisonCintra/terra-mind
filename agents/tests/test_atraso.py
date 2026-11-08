"""G5: custo da demora na decisão do gerente, calculado em código."""

from datetime import timedelta

import pytest
from google.adk.sessions import InMemorySessionService

from agents.config import Settings
from agents.modelos import CustoAtraso, OsEmRisco, PlanoProposto
from agents.rules.atraso import (
    calcular_custos_atraso,
    fator_atraso,
    formatar_duracao,
    linha_custo,
    texto_lembrete,
    valor_aplicacao,
    vencimento,
)
from agents.rules.validador import ContextoPlano
from agents.runner import Orquestrador
from agents.tests.fakes import (
    AGORA,
    FakeCanal,
    FakeConhecimento,
    FakeRepo,
    avaliacao_violando,
    modelos_padrao,
    montar_cenario,
    os_abacus,
    previsao_chuva,
)
from agents.timeout import verificar_timeouts

SETTINGS = Settings(_env_file=None)
# Demo: 80 mm até 04/11 19:00; a bula pede 4 h sem chuva depois de 8 h de aplicação.
JANELA = AGORA.replace(hour=6) + timedelta(days=2)  # 05/11 06:00


def os_em_risco(**extra) -> OsEmRisco:
    dados = {**os_abacus(), **extra}
    return OsEmRisco(os_id=dados.pop("id"), talhao="Talhão 1 - Sede", area_ha=120, **dados)


def contexto(os: OsEmRisco | None = None) -> ContextoPlano:
    from agents.modelos import ViolacaoOs

    violacao = ViolacaoOs.model_validate(avaliacao_violando()["violacoes"][0])
    return ContextoPlano(
        horas=previsao_chuva(),
        os_por_id={1: os or os_em_risco()},
        violacoes_por_os={1: [violacao]},
        os_suspensas=[1],
    )


def plano(inicio=JANELA) -> PlanoProposto:
    return PlanoProposto(
        opcoes=[{"os_id": 1, "novo_inicio": inicio, "maquina_id": 1, "justificativa": "x"}], mensagem="m"
    )


def test_valor_da_aplicacao_sai_de_codigo():
    assert valor_aplicacao(os_em_risco()) == 6660.0  # 120 ha × 0,3 L/ha × R$ 185
    assert valor_aplicacao(os_em_risco(preco_unitario=None)) is None


def test_janela_vence_no_fim_do_bloco_e_a_proxima_e_o_bloco_seguinte():
    validos = [JANELA + timedelta(hours=h) for h in range(13)]  # 06:00 a 18:00
    validos += [JANELA + timedelta(days=1, hours=h) for h in range(13)]
    vence, proxima = vencimento(JANELA, validos)
    assert vence == JANELA + timedelta(hours=10)  # último início 18:00 menos 2 h de antecedência
    assert proxima == JANELA + timedelta(days=1)
    assert vencimento(JANELA + timedelta(days=1), validos)[1] is None  # último bloco da previsão


async def test_custo_de_perder_a_janela_da_demo():
    [c] = await calcular_custos_atraso(plano(), contexto(), FakeConhecimento(), SETTINGS, AGORA)
    assert c.vence_em == JANELA + timedelta(hours=10)  # 05/11 16:00
    assert c.proxima_janela == JANELA + timedelta(days=1)  # 06/11 06:00
    assert c.custo_perder_janela_rs == 133.2  # R$ 6.660 × 0,02 por dia × 1 dia
    assert c.fonte_fator.startswith("estimativa")


async def test_fator_de_atraso_do_conhecimento_tem_prioridade():
    conhecimento = FakeConhecimento(
        fatores={("fungicida", "atraso"): {"fator": 0.05, "fonte_ou_estimativa": "embrapa-x:p.3"}}
    )
    assert await fator_atraso(os_em_risco(), conhecimento, SETTINGS) == (0.05, "embrapa-x:p.3")
    [c] = await calcular_custos_atraso(plano(), contexto(), conhecimento, SETTINGS, AGORA)
    assert c.custo_perder_janela_rs == 333.0


async def test_os_sem_preco_nao_inventa_custo():
    os = os_em_risco(preco_unitario=None)
    [c] = await calcular_custos_atraso(plano(), contexto(os), FakeConhecimento(), SETTINGS, AGORA)
    assert c.custo_perder_janela_rs is None and c.fonte_fator == "OS sem dose ou preço"
    assert "custo não calculado" in linha_custo(c, AGORA)


def test_formatar_duracao():
    assert formatar_duracao(timedelta(minutes=45)) == "45 min"
    assert formatar_duracao(timedelta(hours=5, minutes=59)) == "5 h"
    assert formatar_duracao(timedelta(hours=27)) == "1 dia e 3 h"
    assert formatar_duracao(timedelta(hours=48)) == "2 dias"


def _custo(**extra) -> CustoAtraso:
    dados = {
        "os_id": 1,
        "talhao": "Talhão 1 - Sede",
        "valor_aplicacao_rs": 6660.0,
        "fator_dia": 0.02,
        "fonte_fator": "estimativa: x",
        "janela_proposta": JANELA,
        "vence_em": JANELA + timedelta(hours=10),
        "proxima_janela": JANELA + timedelta(days=1),
        "custo_perder_janela_rs": 133.2,
    }
    return CustoAtraso(**{**dados, **extra})


def test_lembrete_traz_valor_e_horario_em_que_a_janela_vence():
    criado = AGORA
    agora = AGORA + timedelta(hours=3)
    texto = texto_lembrete(7, 1, criado, agora, [_custo()])
    assert "sem decisão no plano 1 há 3 h" in texto
    assert "a janela de quinta, 05/11 às 06:00 vence em 2 dias e 5 h" in texto
    assert "a próxima janela boa é sexta, 06/11 às 06:00" in texto
    assert "esperar até lá custa R$ 133,20 (estimativa)" in texto


def test_lembrete_de_janela_vencida_e_sem_proxima():
    vencida = linha_custo(_custo(), JANELA + timedelta(hours=11))
    assert "já venceu" in vencida and "Ver opções" in vencida
    sem_proxima = linha_custo(_custo(proxima_janela=None, custo_perder_janela_rs=None), AGORA)
    assert "não há outra janela boa" in sem_proxima and "cada dia de atraso custa R$ 133,20" in sem_proxima


async def test_timeout_usa_os_custos_gravados_no_plano():
    repo, canal = FakeRepo(), FakeCanal()
    custos = [_custo().model_dump(mode="json")]
    await repo.criar_plano(7, {"opcoes": [], "mensagem": "m", "custos_atraso": custos})
    assert await verificar_timeouts(repo, canal, AGORA + timedelta(minutes=31), 30) == [1]
    [alerta] = canal.alertas
    assert "seguem suspensas" in alerta and "R$ 133,20" in alerta and "vence em" in alerta
    [auditado] = [a for a in repo.auditoria if a["acao"] == "escalonar_aprovacao"]
    assert auditado["justificativa"] == alerta  # o painel mostra o mesmo texto na linha do tempo


async def test_timeout_com_plano_antigo_sem_custos():
    repo, canal = FakeRepo(), FakeCanal()
    await repo.criar_plano(7, {"opcoes": [], "custos_atraso": [{"quebrado": True}]})
    assert await verificar_timeouts(repo, canal, AGORA + timedelta(minutes=31), 30) == [1]
    assert "seguem suspensas" in canal.alertas[0]


async def test_plano_do_fluxo_grava_o_custo_do_atraso():
    c = montar_cenario()
    orq = Orquestrador(c.deps, InMemorySessionService())
    await orq.iniciar(7)
    [custo] = c.repo.planos[1]["opcoes_json"]["custos_atraso"]
    assert custo["custo_perder_janela_rs"] == 133.2
    assert custo["vence_em"] == (JANELA + timedelta(hours=10)).isoformat()
    [pedido] = [a for a in c.repo.auditoria if a["acao"] == "pedir_aprovacao"]
    assert pedido["depois"]["custos_atraso"] == [custo]


async def test_sem_suspensao_a_decisao_manual_mostra_a_perda_inteira():
    c = montar_cenario(modelos_padrao(avaliador=[RuntimeError("OpenAI fora do ar")]))
    orq = Orquestrador(c.deps, InMemorySessionService())
    await orq.iniciar(7)
    assert c.erp.status(1) == "agendada"
    alerta = c.canal.alertas[-1]
    assert "continuam AGENDADAS" in alerta
    assert "com a chuva prevista, a perda é de R$ 3.330,00 (estimativa)" in alerta


@pytest.mark.parametrize("custos", [None, []])
async def test_custo_ausente_nao_quebra_o_lembrete(custos):
    assert "\n" not in texto_lembrete(7, 1, AGORA, AGORA + timedelta(hours=1), custos or [])
