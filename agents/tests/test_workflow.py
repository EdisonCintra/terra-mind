from datetime import timedelta

import pytest
from google.adk.sessions import InMemorySessionService

from agents.modelos import Resposta
from agents.runner import Orquestrador
from agents.tests.fakes import (
    AGORA,
    FakeLlm,
    avaliacao_violando,
    chamar,
    modelos_padrao,
    montar_cenario,
    os_abacus,
    roteiro_avaliador,
    supervisor_invalido,
    supervisor_que_escolhe,
    ultima_resposta_de,
)
from agents.workflow import APP_NAME

# 05/11 06:00: a chuva vai até 04/11 19:00 e a bula pede 4 h sem chuva depois das 8 h de aplicação.
JANELA_1 = AGORA.replace(hour=6) + timedelta(days=2)


async def iniciar(cenario):
    orq = Orquestrador(cenario.deps, InMemorySessionService())
    assert await orq.iniciar(7) is True
    return orq


async def responder(orq, plano_id, resposta):
    return await orq.responder(Resposta(7, plano_id, resposta, "gerente"), esperar=True)


async def estado(orq):
    s = await orq.session_service.get_session(
        app_name=APP_NAME, user_id="terramind", session_id="incidente-7"
    )
    return s.state


async def test_fluxo_completo_com_sim():
    c = montar_cenario()
    orq = await iniciar(c)

    assert c.erp.status(1) == "suspensa_preventiva"
    assert c.erp.status(2) == "agendada"  # fora da janela de risco: intocada
    assert c.repo.incidentes[7].status == "aguardando_aprovacao"
    [(incidente_id, plano_id, texto)] = c.canal.aprovacoes
    assert (incidente_id, plano_id) == (7, 1)
    assert "R$ 3.330,00" in texto  # 120 ha × 0,3 L/ha × R$ 185 × 0,5, montado em código
    assert "bula-abacus-hc, p. 7" in texto
    assert "05/11 às 06:00" in texto
    assert c.conhecimento.buscas and c.conhecimento.buscas[0][1] == "Abacus HC"

    assert await responder(orq, 1, "SIM") == "ok"

    assert c.erp.status(1) == "reagendada"
    assert c.erp.os[1]["inicio_previsto"] == JANELA_1
    assert c.erp.estoque[1] == pytest.approx(500 - 120 * 0.3)  # 36 L de Abacus HC reservados
    assert c.canal.operadores and c.canal.operadores[0][0] == 999
    assert c.repo.incidentes[7].status == "encerrado"
    assert "executor:executar_plano" in c.repo.acoes()


async def test_rodape_usa_numeros_do_codigo_e_nao_do_llm():
    c = montar_cenario(
        modelos_padrao(supervisor=[supervisor_que_escolhe(mensagem="Perda de R$ 1,00. Página 99.")])
    )
    await iniciar(c)
    texto = c.canal.aprovacoes[0][2]
    assert "Perda evitada: R$ 3.330,00" in texto
    assert "p. 7" in texto


async def test_resposta_repetida_nao_reexecuta():
    c = montar_cenario()
    orq = await iniciar(c)
    assert await responder(orq, 1, "SIM") == "ok"
    escritas = len(c.erp.escritas)
    assert await responder(orq, 1, "SIM") == "repetida"
    assert await responder(orq, 99, "SIM") == "desconhecida"
    assert len(c.erp.escritas) == escritas


async def test_nao_mantem_suspensa_e_pede_decisao_manual():
    c = montar_cenario()
    orq = await iniciar(c)
    await responder(orq, 1, "NAO")
    assert c.erp.status(1) == "suspensa_preventiva"
    assert c.repo.incidentes[7].status == "aguardando_decisao_manual"
    assert "recusou" in c.canal.alertas[-1]


async def test_ver_opcoes_replaneja_duas_vezes_e_depois_pede_decisao_manual():
    c = montar_cenario()
    orq = await iniciar(c)
    await responder(orq, 1, "OPCOES")
    await responder(orq, 2, "OPCOES")
    assert [p for _, p, _ in c.canal.aprovacoes] == [1, 2, 3]
    datas = [c.repo.planos[p]["opcoes_json"]["opcoes"][0]["novo_inicio"] for p in (1, 2, 3)]
    assert len(set(datas)) == 3  # nunca repete uma data já oferecida

    await responder(orq, 3, "OPCOES")
    assert c.repo.incidentes[7].status == "aguardando_decisao_manual"
    assert c.erp.status(1) == "suspensa_preventiva"
    assert "esgotaram" in c.canal.alertas[-1]


async def test_avaliador_falha_nao_suspende():
    c = montar_cenario(modelos_padrao(avaliador=[RuntimeError("OpenAI fora do ar")]))
    await iniciar(c)
    assert c.erp.status(1) == "agendada"
    assert ("suspender_os", 1) not in c.erp.escritas
    assert c.canal.aprovacoes == []
    assert c.repo.incidentes[7].status == "aguardando_decisao_manual"
    assert "continuam AGENDADAS" in c.canal.alertas[-1]
    assert "avaliador:avaliacao_falhou" in c.repo.acoes()
    assert "runner:falha_interna" not in c.repo.acoes()


async def test_avaliacao_sem_citacao_e_tratada_como_falha():
    sem_fonte = avaliacao_violando(fonte_doc=None, pagina=None)
    c = montar_cenario(modelos_padrao(avaliador=roteiro_avaliador(sem_fonte)))
    await iniciar(c)
    assert c.erp.status(1) == "agendada"
    assert c.repo.incidentes[7].status == "aguardando_decisao_manual"
    # Tratada no grafo (proteção do agente + rota `falhou`), não pela rede de segurança do runner.
    assert "avaliador:avaliacao_falhou" in c.repo.acoes()
    assert "runner:falha_interna" not in c.repo.acoes()


async def test_avaliador_em_loop_de_ferramenta_para_e_nao_suspende():
    from agents.agentes._comum import MAX_RODADAS_FERRAMENTA

    sempre_busca = chamar("buscar_trechos", consulta="chuva", produto="Abacus HC")
    c = montar_cenario(modelos_padrao(avaliador=[sempre_busca]))
    await iniciar(c)
    # O fake chama buscar_trechos até na rodada em que ela já não é oferecida; a seguinte vira falha.
    assert len(c.conhecimento.buscas) == MAX_RODADAS_FERRAMENTA + 1
    assert c.erp.status(1) == "agendada"
    assert c.repo.incidentes[7].status == "aguardando_decisao_manual"
    assert "avaliador:avaliacao_falhou" in c.repo.acoes()
    assert "runner:falha_interna" not in c.repo.acoes()


async def test_avaliador_na_ultima_rodada_so_pode_responder():
    from agents.agentes._comum import MAX_RODADAS_FERRAMENTA

    ferramentas_por_chamada = []

    def busca_ate_ficar_sem_ferramenta(req):
        nomes = [d.name for t in req.config.tools or [] for d in t.function_declarations or []]
        ferramentas_por_chamada.append(nomes)
        if "buscar_trechos" in nomes:
            return chamar("buscar_trechos", consulta="chuva", produto="Abacus HC")
        return avaliacao_violando()

    c = montar_cenario(modelos_padrao(avaliador=[busca_ate_ficar_sem_ferramenta]))
    await iniciar(c)
    assert len(ferramentas_por_chamada) == MAX_RODADAS_FERRAMENTA + 1
    assert "buscar_trechos" not in ferramentas_por_chamada[-1]
    assert "avaliador:avaliar_risco" in c.repo.acoes()
    assert c.erp.status(1) == "suspensa_preventiva"


async def test_sem_violacao_encerra_sem_mexer_na_os():
    nao_viola = {"violacoes": [{"os_id": 1, "viola": False, "justificativa": "documento não traz limite"}]}
    c = montar_cenario(modelos_padrao(avaliador=roteiro_avaliador(nao_viola)))
    await iniciar(c)
    assert c.erp.status(1) == "agendada"
    assert c.repo.incidentes[7].status == "encerrado"
    assert c.canal.alertas == []


async def test_sem_os_na_janela_encerra_sem_chamar_llm():
    c = montar_cenario()
    c.erp.os[1] = os_abacus(inicio_previsto=AGORA + timedelta(days=5))
    await iniciar(c)
    assert c.repo.incidentes[7].status == "encerrado"
    assert c.deps.modelos["avaliador"].chamadas == 0


async def test_erp_fora_do_ar_pede_decisao_manual():
    c = montar_cenario()
    c.erp.indisponivel = True
    await iniciar(c)
    assert c.repo.incidentes[7].status == "aguardando_decisao_manual"
    assert "ERP" in c.canal.alertas[-1]


async def test_validador_reprova_e_supervisor_corrige():
    madrugada_chuvosa = AGORA + timedelta(hours=20)
    roteiro = [supervisor_invalido(madrugada_chuvosa), supervisor_que_escolhe()]
    c = montar_cenario(modelos_padrao(supervisor=roteiro))
    await iniciar(c)
    vereditos = [
        a["depois"]["veredito"]["aprovado"] for a in c.repo.auditoria if a["acao"] == "validar_plano"
    ]
    assert vereditos == [False, True]
    assert len(c.canal.aprovacoes) == 1


async def test_validador_reprova_duas_vezes_pede_decisao_manual():
    madrugada_chuvosa = AGORA + timedelta(hours=20)
    c = montar_cenario(modelos_padrao(supervisor=[supervisor_invalido(madrugada_chuvosa)]))
    await iniciar(c)
    assert c.canal.aprovacoes == []
    assert c.erp.status(1) == "suspensa_preventiva"
    assert c.repo.incidentes[7].status == "aguardando_decisao_manual"
    assert "Validador" in c.canal.alertas[-1]


async def test_especialista_falha_e_o_fluxo_segue():
    c = montar_cenario(modelos_padrao(agronomico=[RuntimeError("timeout")]))
    orq = await iniciar(c)
    assert len(c.canal.aprovacoes) == 1
    assert "indisponível" in (await estado(orq))["analise_agronomica"]


async def test_sim_atrasado_com_maquina_ocupada_replaneja():
    c = montar_cenario()
    orq = await iniciar(c)
    # Enquanto o gerente não respondia, outra OS ocupou a máquina 1 na janela aprovada.
    c.erp.os[3] = os_abacus(id=3, talhao_id=1, inicio_previsto=JANELA_1, maquina_id=1)
    c.erp.os[3]["status"] = "agendada"
    await responder(orq, 1, "SIM")
    assert any("não vale mais" in a for a in c.canal.alertas)
    assert [p for _, p, _ in c.canal.aprovacoes] == [1, 2]
    novo = c.repo.planos[2]["opcoes_json"]["opcoes"][0]
    assert novo["novo_inicio"] != JANELA_1.isoformat() or novo["maquina_id"] != 1
    assert c.erp.status(1) == "suspensa_preventiva"


async def test_falha_na_execucao_pede_decisao_manual():
    c = montar_cenario()
    c.erp.falhar_em.add("reservar_estoque")
    orq = await iniciar(c)
    await responder(orq, 1, "SIM")
    assert c.repo.incidentes[7].status == "aguardando_decisao_manual"
    alerta = c.canal.alertas[-1]
    # A OS já foi reagendada antes da falha: o gerente precisa saber, e não ler "segue suspensa".
    assert "parou no meio" in alerta and "OS 1: reagendar_os" in alerta and "reservar_estoque" in alerta
    assert "SUSPENSA" not in alerta


async def test_iniciar_duas_vezes_nao_duplica():
    c = montar_cenario()
    orq = await iniciar(c)
    assert await orq.iniciar(7) is False
    assert len(c.canal.aprovacoes) == 1


async def test_avaliador_recebe_resposta_da_ferramenta():
    """O Avaliador chama buscar_trechos e o trecho volta para ele na mesma execução."""
    vistos = []

    def decide(req):
        from agents.tests.fakes import ultima_resposta_de

        vistos.append(ultima_resposta_de(req, "buscar_trechos"))
        return avaliacao_violando()

    c = montar_cenario(
        modelos_padrao(avaliador=[chamar("buscar_trechos", consulta="chuva", produto="Abacus HC"), decide])
    )
    await iniciar(c)
    assert vistos[0]["trechos"][0]["pagina"] == 7


async def test_llm_fake_unico_por_agente():
    c = montar_cenario()
    assert all(isinstance(m, FakeLlm) for m in c.deps.modelos.values())


async def test_supervisor_com_saida_invalida_e_revisado_no_grafo():
    roteiro = [{"opcoes": "não é lista", "mensagem": 1}, supervisor_que_escolhe()]
    c = montar_cenario(modelos_padrao(supervisor=roteiro))
    await iniciar(c)
    assert len(c.canal.aprovacoes) == 1
    assert "runner:falha_interna" not in c.repo.acoes()


async def test_grafo_tem_os_agentes_como_nos():
    from agents.workflow import criar_workflow

    nomes = {n.name for n in criar_workflow(montar_cenario().deps).graph.nodes}
    assert {"avaliador_risco", "agronomico", "operacional", "supervisor"} <= nomes


async def test_ferramenta_que_levanta_vira_ok_false_para_o_llm():
    from agents.clientes.conhecimento import ConhecimentoIndisponivel

    vistos = []

    def decide(req):
        vistos.append(ultima_resposta_de(req, "buscar_trechos"))
        return avaliacao_violando()

    c = montar_cenario(modelos_padrao(avaliador=[chamar("buscar_trechos", consulta="chuva"), decide]))

    async def fora_do_ar(*a, **k):
        raise ConhecimentoIndisponivel("conhecimento fora do ar")

    c.conhecimento.buscar_trechos = fora_do_ar
    await iniciar(c)
    assert vistos[0]["ok"] is False and "fora do ar" in vistos[0]["erro"]
    assert "runner:falha_interna" not in c.repo.acoes()


async def test_os_com_duas_violacoes_e_suspensa_uma_vez():
    vento = avaliacao_violando(parametro="vento", limite=5, unidade="km/h", janela_h=None)["violacoes"][0]
    duas = avaliacao_violando()
    duas["violacoes"].append(vento)
    c = montar_cenario(modelos_padrao(avaliador=roteiro_avaliador(duas)))
    await iniciar(c)
    assert [e for e in c.erp.escritas if e[0] == "suspender_os"] == [("suspender_os", 1)]
    estado_impactos = [a for a in c.repo.auditoria if a["acao"] == "suspender_os"][0]["depois"]
    assert estado_impactos["suspensas"] == [1] and len(estado_impactos["impactos"]) == 1


async def test_alerta_final_so_fala_de_estoque_quando_reservou():
    c = montar_cenario()
    c.erp.os[1]["insumo_id"] = None
    orq = await iniciar(c)
    await responder(orq, 1, "SIM")
    assert "executado" in c.canal.alertas[-1] and "estoque" not in c.canal.alertas[-1]


async def test_resposta_gravada_sem_retomada_pode_ser_reenviada():
    c = montar_cenario()
    orq = await iniciar(c)
    # A resposta foi gravada, mas o processo caiu antes de retomar a sessão.
    await c.repo.registrar_resposta(1, 7, "SIM", "gerente")
    assert await responder(orq, 1, "SIM") == "ok"
    assert c.erp.status(1) == "reagendada"
    assert await responder(orq, 1, "SIM") == "repetida"  # agora a sessão não está mais pausada


async def test_reenvio_diferente_do_gravado_nao_retoma():
    c = montar_cenario()
    orq = await iniciar(c)
    await c.repo.registrar_resposta(1, 7, "SIM", "gerente")
    assert await responder(orq, 1, "NAO") == "repetida"
    assert c.erp.status(1) == "suspensa_preventiva"
