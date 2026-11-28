"""Assistente do chat: responde com os dados do painel, só das fazendas de quem pergunta, e com a bula
conferida. O LLM é roteirizado (`FakeLlm`); as consultas ao banco são falsas (as reais: test_consultas)."""

from datetime import timedelta

from agents.assistente import FALHA, SO_BULA, Assistente, ler_contato, resumo_previsao
from agents.bula import PergunteBula
from agents.clientes.consultas import Escopo
from agents.tests.fakes import AGORA, chamar, modelos_padrao, montar_cenario, ultima_resposta_de

GERENTE = "telegram:111"


class FakeConsultas:
    """Fazenda 1: Talhão 1 (gerente 111 no Telegram). Fazenda 2: Talhão 9 (de outro gerente)."""

    def __init__(self):
        self.escopos: list[Escopo] = []
        self.talhoes_ = [
            {"id": 1, "nome": "Talhão 1 - Sede", "fazenda_id": 1},
            {"id": 9, "nome": "Talhão 9 - Vizinho", "fazenda_id": 2},
        ]
        self.ordens_ = [
            {"id": 1, "talhao": "Talhão 1 - Sede", "status": "suspensa_preventiva", "fazenda_id": 1},
            {"id": 7, "talhao": "Talhão 9 - Vizinho", "status": "suspensa_preventiva", "fazenda_id": 2},
        ]

    def _filtrar(self, escopo, linhas):
        self.escopos.append(escopo)
        return [li for li in linhas if escopo.todas or li["fazenda_id"] in escopo.fazendas]

    async def escopo_do_contato(self, canal, contato):
        return Escopo((1,)) if (canal, contato) == ("telegram", 111) else Escopo()

    async def fazendas(self, escopo):
        return [{"id": 1, "nome": "Fazenda Exemplo"}]

    async def talhoes(self, escopo):
        return self._filtrar(escopo, self.talhoes_)

    async def ordens(self, escopo, desde, ate, status, talhao_id):
        return [o for o in self._filtrar(escopo, self.ordens_) if status is None or o["status"] == status]

    async def ordem(self, escopo, os_id):
        return next((o for o in self._filtrar(escopo, self.ordens_) if o["id"] == os_id), None)

    async def incidentes(self, escopo, status, limite):
        return []

    async def incidente(self, escopo, incidente_id):
        return None

    async def planos_pendentes(self, escopo):
        return []

    async def perda_evitada(self, escopo, desde, incluir_simulacoes):
        return [{"situacao": "evitada", "ordens": 1, "perda_rs": 3330.0}]

    async def insumos(self, escopo):
        return [{"id": 1, "fazenda_id": 1, "produto": "Abacus HC", "estoque": 500.0}]

    async def maquinas(self, escopo):
        return []

    async def previsao(self, escopo, talhao_id):
        return None


def montar(roteiro: list, **modelos):
    c = montar_cenario(modelos_padrao(assistente=roteiro, **modelos))
    consultas = FakeConsultas()
    return c, consultas, Assistente(c.deps, consultas, PergunteBula(c.deps))


def responde_com(ferramenta: str, formatar):
    """Segunda chamada do LLM: lê a resposta da ferramenta na requisição e escreve o texto final."""
    return lambda req: formatar(ultima_resposta_de(req, ferramenta))


# ------------------------------------------------------------------------------ escopo e acesso


async def test_gerente_ve_so_as_ordens_das_fazendas_dele():
    _, consultas, assistente = montar(
        [
            chamar("listar_ordens", status="suspensa_preventiva"),
            responde_com(
                "listar_ordens", lambda r: "Suspensas: " + ", ".join(o["talhao"] for o in r["ordens"])
            ),
        ]
    )
    texto = await assistente.responder_no_canal("Quais OS estão suspensas?", "Maria", contato=GERENTE)
    assert texto == "Suspensas: Talhão 1 - Sede"
    assert consultas.escopos == [Escopo((1,))]


async def test_terminal_ve_todas_as_fazendas():
    _, consultas, assistente = montar(
        [chamar("listar_talhoes"), responde_com("listar_talhoes", lambda r: str(len(r["talhoes"])))]
    )
    assert await assistente.responder_no_canal("Quantos talhões?", "terminal", contato="terminal") == "2"
    assert consultas.escopos == [Escopo(todas=True)]


async def test_contato_que_nao_e_gerente_so_consulta_a_bula():
    _, consultas, assistente = montar(
        [chamar("listar_ordens"), responde_com("listar_ordens", lambda r: r["erro"])]
    )
    texto = await assistente.responder_no_canal("Quais OS?", "Chat extra", contato="telegram:999")
    assert texto == SO_BULA
    assert consultas.escopos == []  # nenhuma consulta ao banco


async def test_sem_contato_tambem_so_bula():
    assistente = montar(["ok"])[2]
    assert await assistente.escopo(None) == Escopo()
    assert await assistente.escopo("whatsapp:5564999998888") == Escopo()


def test_ler_contato():
    assert ler_contato("telegram:111") == ("telegram", 111)
    assert ler_contato("whatsapp:5564999998888") == ("whatsapp", 5564999998888)
    assert ler_contato("terminal") == ("terminal", None)
    assert ler_contato(None) == ("", None)


# ------------------------------------------------------------------------------ bula e conversa


async def test_bula_passa_pela_conferencia_e_volta_com_a_pagina():
    _, _, assistente = montar(
        [
            chamar("perguntar_a_bula", pergunta="Quanto tempo sem chuva?", produto="Abacus HC"),
            responde_com("perguntar_a_bula", lambda r: r["resposta"]),
        ]
    )
    texto = await assistente.responder_no_canal("Abacus HC aguenta chuva?", "Maria", contato=GERENTE)
    assert "p. 7" in texto and "Abacus HC" in texto


async def test_conversa_lembra_a_pergunta_anterior():
    def segunda(req):
        perguntas = [p.text for c in req.contents if c.role == "user" for p in c.parts if p.text]
        return " | ".join(perguntas)

    _, _, assistente = montar(["primeira resposta", segunda])
    await assistente.responder_no_canal("Quais OS estão suspensas?", "Maria", contato=GERENTE)
    texto = await assistente.responder_no_canal("E a do Talhão 1?", "Maria", contato=GERENTE)
    assert texto == "Quais OS estão suspensas? | E a do Talhão 1?"


async def test_conversas_de_contatos_diferentes_nao_se_misturam():
    def eco(req):
        return str(sum(1 for c in req.contents if c.role == "user"))

    _, _, assistente = montar([eco])
    await assistente.responder_no_canal("oi", "Maria", contato=GERENTE)
    assert await assistente.responder_no_canal("oi", "Terminal", contato="terminal") == "1"


async def test_conversa_parada_recomeca_do_zero(monkeypatch):
    import agents.assistente as modulo

    def eco(req):
        return str(sum(1 for c in req.contents if c.role == "user"))

    _, _, assistente = montar([eco])
    relogio = [1000.0]
    monkeypatch.setattr(modulo.time, "monotonic", lambda: relogio[0])
    assert await assistente.responder_no_canal("oi", "Maria", contato=GERENTE) == "1"
    assert await assistente.responder_no_canal("e aí", "Maria", contato=GERENTE) == "2"
    relogio[0] += modulo.CONVERSA_MIN * 60 + 1
    assert await assistente.responder_no_canal("de novo", "Maria", contato=GERENTE) == "1"


# ------------------------------------------------------------------------------ falhas e limites


async def test_modelo_fora_do_ar_responde_sem_inventar():
    _, _, assistente = montar([RuntimeError("OpenAI fora do ar")])
    assert await assistente.responder_no_canal("Quais OS?", "Maria", contato=GERENTE) == FALHA


async def test_loop_de_ferramentas_e_cortado_e_o_modelo_responde():
    def insistente(req):
        if req.config.tools:
            return chamar("listar_talhoes")
        return "respondi com o que tinha"

    _, consultas, assistente = montar([insistente])
    texto = await assistente.responder_no_canal("Talhões?", "Maria", contato=GERENTE)
    assert texto == "respondi com o que tinha"
    assert len(consultas.escopos) == 8  # MAX_RODADAS_FERRAMENTA


async def test_ferramenta_que_quebra_vira_erro_para_o_modelo():
    _, consultas, assistente = montar(
        [chamar("listar_talhoes"), responde_com("listar_talhoes", lambda r: f"ok={r['ok']}")]
    )

    async def quebra(escopo):
        raise RuntimeError("banco fora")

    consultas.talhoes = quebra
    assert await assistente.responder_no_canal("Talhões?", "Maria", contato=GERENTE) == "ok=False"


# ------------------------------------------------------------------------------ previsão


def test_resumo_previsao_soma_chuva_e_ignora_valores_ausentes():
    def hora(h, **valores):
        return {"ts": (AGORA + timedelta(hours=h)).isoformat(), **valores}

    horas = [
        hora(h, chuva_mm=2.0, vento_kmh=5 + h, umidade_pct=80, temp_c=20 + h, rajada_kmh=None)
        for h in range(3)
    ]
    horas.append(hora(3, chuva_mm=None, vento_kmh=None, umidade_pct=None, temp_c=None))  # hora sem dados
    [bloco] = resumo_previsao(horas, AGORA, horas=3)  # 08:00 a 11:00, todas no bloco das 06:00
    assert bloco["bloco"] == "03/11 06:00–12:00"
    assert bloco["chuva_mm"] == 6.0 and bloco["vento_max_kmh"] == 7 and bloco["temp_min_c"] == 20
    assert bloco["rajada_max_kmh"] is None and bloco["inversao"] is None


def test_resumo_previsao_fica_na_janela_pedida():
    horas = [{"ts": (AGORA + timedelta(hours=h)).isoformat(), "chuva_mm": 1} for h in range(0, 100, 6)]
    assert len(resumo_previsao(horas, AGORA, horas=24)) == 5
