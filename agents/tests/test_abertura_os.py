"""Abrir OS pelo chat: o Operacional monta, o código confere e só grava depois da confirmação do gerente.

LLMs roteirizados (`FakeLlm`) para o assistente e para o Operacional; ERP e consultas falsos. No cenário,
a OS 1 ocupa o Pulverizador A de 04/11 04:00 a 12:00; o Pulverizador B está livre.
"""

from datetime import timedelta

from agents.abertura_os import confirmou
from agents.assistente import Assistente
from agents.bula import PergunteBula
from agents.clientes.consultas import Escopo
from agents.tests.fakes import AGORA, chamar, modelos_padrao, montar_cenario, ultima_resposta_de

GERENTE = "telegram:111"
AMANHA_7H = (AGORA + timedelta(days=1)).replace(hour=7)  # 04/11 07:00, choca com a OS 1 no Pulverizador A
PEDIDO = "Pulverizar Abacus HC no Talhão 1 amanhã às 7h, 0,3 L/ha"


class FakeConsultas:
    """Fazenda 1: Talhão 1 e Abacus HC (gerente 111). Fazenda 2: Talhão 9 e o estoque dela."""

    async def escopo_do_contato(self, canal, contato):
        return Escopo((1,)) if (canal, contato) == ("telegram", 111) else Escopo()

    async def fazendas(self, escopo):
        return [{"id": 1, "nome": "Fazenda Exemplo"}]

    def _filtrar(self, escopo, linhas):
        return [li for li in linhas if escopo.todas or li["fazenda_id"] in escopo.fazendas]

    async def talhoes(self, escopo):
        return self._filtrar(
            escopo,
            [
                {"id": 1, "nome": "Talhão 1 - Sede", "fazenda_id": 1, "area_ha": 120.0, "cultura": "milho"},
                {"id": 9, "nome": "Talhão 9 - Vizinho", "fazenda_id": 2, "area_ha": 50.0, "cultura": "soja"},
            ],
        )

    async def insumos(self, escopo):
        return self._filtrar(
            escopo,
            [
                {"id": 1, "fazenda_id": 1, "produto": "Abacus HC", "unidade": "L", "estoque": 500.0,
                 "comprometido": 36.0},
                {"id": 3, "fazenda_id": 2, "produto": "Abacus HC", "unidade": "L", "estoque": 200.0,
                 "comprometido": 0.0},
            ],
        )  # fmt: skip

    async def ordens(self, escopo, desde, ate, status, talhao_id):
        return []


def proposta(**campos) -> dict:
    base = {
        "pronta": True,
        "talhao_id": 1,
        "insumo_id": 1,
        "dose_por_ha": 0.3,
        "inicio": AMANHA_7H.isoformat(),
        "duracao_horas": 4,
        "maquina_id": 2,
        "operador_id": 1,
        "justificativa": "Pulverizador B livre; A está na OS 1. Dose pedida pelo gerente.",
    }
    return base | campos


def montar(assistente: list, operacional: list):
    c = montar_cenario(modelos_padrao(assistente=assistente, operacional=operacional))
    return c, Assistente(c.deps, FakeConsultas(), PergunteBula(c.deps))


def responde_com(ferramenta: str, formatar):
    return lambda req: formatar(ultima_resposta_de(req, ferramenta))


def abertas(c) -> list[tuple]:
    return [e for e in c.erp.escritas if e[0] == "abrir_os"]


# -------------------------------------------------------------------------------- fluxo feliz


async def test_propoe_e_so_abre_a_os_depois_do_confirmo():
    c, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido=PEDIDO),
            responde_com(
                "abrir_ordem_servico",
                lambda r: " ".join(r["resumo"][k] for k in ("maquina", "inicio", "quantidade_total")),
            ),
            chamar("confirmar_ordem_servico"),
            responde_com("confirmar_ordem_servico", lambda r: f"OS {r['os_id']} aberta"),
        ],
        [proposta()],
    )
    texto = await assistente.responder_no_canal(PEDIDO, "Maria", contato=GERENTE)
    assert texto == "Pulverizador B 04/11 07:00 36.0 L"
    assert abertas(c) == []  # proposta não grava

    assert await assistente.responder_no_canal("Confirmo", "Maria", contato=GERENTE) == "OS 3 aberta"
    nova = c.erp.os[3]
    assert nova["status"] == "agendada" and nova["maquina_id"] == 2 and nova["inicio_previsto"] == AMANHA_7H
    assert abertas(c)[0][-1] == "Maria (telegram:111)"


async def test_resumo_avisa_quando_o_estoque_livre_nao_cobre_a_os():
    _, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido=PEDIDO),
            responde_com("abrir_ordem_servico", lambda r: r["resumo"].get("aviso_estoque", "sem aviso")),
        ],
        [proposta(dose_por_ha=3.9)],  # 468 L de 500, mas 36 L já estão comprometidos
    )
    texto = await assistente.responder_no_canal(PEDIDO, "Maria", contato=GERENTE)
    assert texto.startswith("descontadas as OS já agendadas, sobram 464.0 L")


# ------------------------------------------------------------------------ garantias em código


async def test_nao_confirma_na_mesma_mensagem_em_que_propos():
    c, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido=PEDIDO),
            chamar("confirmar_ordem_servico"),
            responde_com("confirmar_ordem_servico", lambda r: r["erro"]),
        ],
        [proposta()],
    )
    texto = await assistente.responder_no_canal(PEDIDO + ", pode confirmar", "Maria", contato=GERENTE)
    assert texto.startswith("mostre a proposta ao gerente")
    assert abertas(c) == []


async def test_sem_confirmacao_explicita_nao_grava():
    c, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido=PEDIDO),
            "Proposta pronta.",
            chamar("confirmar_ordem_servico"),
            responde_com("confirmar_ordem_servico", lambda r: r["erro"]),
        ],
        [proposta()],
    )
    await assistente.responder_no_canal(PEDIDO, "Maria", contato=GERENTE)
    texto = await assistente.responder_no_canal("hmm, não abre ainda", "Maria", contato=GERENTE)
    assert texto.startswith("o gerente não confirmou")
    assert abertas(c) == []


async def test_confirmar_sem_proposta_pendente_nao_grava():
    c, assistente = montar(
        [chamar("confirmar_ordem_servico"), responde_com("confirmar_ordem_servico", lambda r: r["erro"])],
        [proposta()],
    )
    texto = await assistente.responder_no_canal("confirmo", "Maria", contato=GERENTE)
    assert texto.startswith("não há OS proposta")
    assert abertas(c) == []


async def test_conferencia_recusa_maquina_ocupada_e_o_operacional_corrige():
    pedidos_ao_operacional = []

    def segunda(req):
        pedidos_ao_operacional.append(req.config.system_instruction)
        return proposta(maquina_id=2)

    _, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido=PEDIDO),
            responde_com("abrir_ordem_servico", lambda r: r["resumo"]["maquina"]),
        ],
        [proposta(maquina_id=1), segunda],  # A está na OS 1 às 7h
    )
    assert await assistente.responder_no_canal(PEDIDO, "Maria", contato=GERENTE) == "Pulverizador B"
    assert "RECUSADA" in str(pedidos_ao_operacional[0]) and "máquina 1 não está livre" in str(
        pedidos_ao_operacional[0]
    )


async def test_talhao_de_outra_fazenda_nunca_vira_proposta():
    c, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido="Pulverizar o Talhão 9"),
            responde_com("abrir_ordem_servico", lambda r: r["erro"]),
        ],
        [proposta(talhao_id=9, insumo_id=3)],
    )
    texto = await assistente.responder_no_canal("Pulverizar o Talhão 9", "Maria", contato=GERENTE)
    assert "talhão 9 não é das fazendas de quem pediu" in texto
    assert abertas(c) == []


async def test_inicio_de_madrugada_e_recusado():
    _, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido=PEDIDO),
            responde_com("abrir_ordem_servico", lambda r: r["erro"]),
        ],
        [proposta(inicio=AMANHA_7H.replace(hour=3).isoformat())],
    )
    texto = await assistente.responder_no_canal(PEDIDO, "Maria", contato=GERENTE)
    assert "fora de 06:00–18:00" in texto


async def test_ferramentas_do_operacional_ficam_no_escopo_de_quem_pede():
    def ler(req):
        erro = ultima_resposta_de(req, "maquinas_livres")["erro"]
        return {"pronta": False, "falta": [erro]}

    _, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido="Talhão 9"),
            responde_com("abrir_ordem_servico", lambda r: r["falta"][0]),
        ],
        [chamar("maquinas_livres", talhao_id=9, inicio=AMANHA_7H.isoformat()), ler],
    )
    assert await assistente.responder_no_canal("Talhão 9", "Maria", contato=GERENTE) == (
        "talhão 9 não encontrado"
    )


async def test_proposta_incompleta_volta_com_o_que_perguntar():
    _, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido="Pulverizar o Talhão 1"),
            responde_com("abrir_ordem_servico", lambda r: r["falta"][0]),
        ],
        [{"pronta": False, "falta": ["A bula traz 0,25 a 0,5 L/ha (p. 3): qual dose?"]}],
    )
    texto = await assistente.responder_no_canal("Pulverizar o Talhão 1", "Maria", contato=GERENTE)
    assert texto == "A bula traz 0,25 a 0,5 L/ha (p. 3): qual dose?"


async def test_contato_que_nao_e_gerente_nao_abre_os():
    c, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido=PEDIDO),
            responde_com("abrir_ordem_servico", lambda r: r["erro"]),
        ],
        [proposta()],
    )
    texto = await assistente.responder_no_canal(PEDIDO, "Chat extra", contato="telegram:999")
    assert texto == "este contato não é gerente de nenhuma fazenda"
    assert c.deps.modelos["operacional"].chamadas == 0


async def test_erp_recusa_na_gravacao_e_a_proposta_nao_fica_pendente():
    c, assistente = montar(
        [
            chamar("abrir_ordem_servico", pedido=PEDIDO),
            "Proposta pronta.",
            chamar("confirmar_ordem_servico"),
            responde_com("confirmar_ordem_servico", lambda r: r["erro"]),
        ],
        [proposta()],
    )
    c.erp.falhar_em.add("abrir_os")
    await assistente.responder_no_canal(PEDIDO, "Maria", contato=GERENTE)
    assert "já está na OS 1" in await assistente.responder_no_canal("confirmo", "Maria", contato=GERENTE)
    assert 3 not in c.erp.os


def test_confirmou():
    for sim in ("Confirmo", "confirma!", "pode abrir", "ok, abre", "CONFIRMADO"):
        assert confirmou(sim), sim
    for nao in ("não abra", "nao confirmo", "espera", "muda pra tarde", "sim", "", None):
        assert not confirmou(nao), nao
