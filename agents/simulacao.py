"""Fazenda simulada: ERP, conhecimento, canal, repositório e LLM em memória, e o cenário da demo.

Usada pelos testes e pelo modo de demonstração do `adk web` (`agents/agentes/`).
O cenário (D2 e seed do Felipe): 80 mm de chuva nas próximas 36 h e a pulverização de Abacus HC
(fungicida) no Talhão 1 agendada para daqui a 20 h.
"""

import json
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from agents.config import Settings
from agents.deps import Dependencias
from agents.modelos import Gatilho, HoraPrevisao, Incidente
from agents.rules.janelas import FUSO

AGORA = datetime(2026, 11, 3, 8, 0, tzinfo=FUSO)
PRODUTO = "Abacus HC"
# Texto real da bula do Abacus HC (BASF), p. 7, indexada pelo Edison.
TRECHO_BULA = (
    "A ocorrência de chuvas dentro de um período de quatro (4) horas após a aplicação pode afetar o"
    " desempenho do produto."
)


# ---------------------------------------------------------------- LLM


class FakeLlm(BaseLlm):
    """Devolve respostas roteirizadas, uma por chamada ao modelo.

    Cada item é texto, um `types.Part` (ex.: chamada de ferramenta), uma exceção (levantada) ou uma
    função `(LlmRequest) -> item`, que pode olhar as respostas das ferramentas na requisição.
    """

    model: str = "fake"
    roteiro: list = []
    chamadas: int = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        item = self.roteiro[min(self.chamadas, len(self.roteiro) - 1)]
        self.chamadas += 1
        if callable(item) and not isinstance(item, type):
            item = item(llm_request)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, types.Part):
            yield LlmResponse(content=types.Content(role="model", parts=[item]))
            return
        if not isinstance(item, str):
            item = json.dumps(item, default=str)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=item)]))


def chamar(ferramenta: str, **args: Any) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=ferramenta, args=args))


def ultima_resposta_de(req: LlmRequest, ferramenta: str) -> dict | None:
    for conteudo in reversed(req.contents or []):
        for parte in conteudo.parts or []:
            fr = parte.function_response
            if fr is not None and fr.name == ferramenta:
                return fr.response
    return None


def avaliacao_violando(os_id: int = 1, limite: float = 0, janela_h: float = 4, **extra) -> dict:
    v = {
        "os_id": os_id,
        "viola": True,
        "parametro": "chuva",
        "limite": limite,
        "unidade": "mm",
        "janela_h": janela_h,
        "valor_previsto": 80,
        "trecho": TRECHO_BULA,
        "fonte_doc": "bula-abacus-hc",
        "pagina": 7,
        "justificativa": "80 mm previstos nas 36 h seguintes; a bula pede 4 h sem chuva após a aplicação.",
    }
    v.update(extra)
    return {"violacoes": [v]}


def roteiro_avaliador(avaliacao: dict | Exception | None = None) -> list:
    return [
        chamar("buscar_trechos", consulta="chuva após a aplicação", produto=PRODUTO),
        avaliacao or avaliacao_violando(),
    ]


def supervisor_que_escolhe(
    indice: int = 0, mensagem: str = "Chuva forte prevista; pulverização suspensa."
) -> Callable:
    """Chama janelas_validas para a OS 1 e propõe a janela `indice` com a primeira máquina livre."""

    def responder(req: LlmRequest):
        janelas = ultima_resposta_de(req, "janelas_validas")
        if janelas is None:
            return chamar("janelas_validas", os_id=1)
        escolhida = janelas["janelas"][indice]
        return {
            "opcoes": [
                {
                    "os_id": 1,
                    "novo_inicio": escolhida["inicio"],
                    "maquina_id": escolhida["maquinas_livres"][0],
                    "justificativa": "primeira janela sem chuva",
                }
            ],
            "mensagem": mensagem,
        }

    return responder


def supervisor_invalido(novo_inicio: datetime) -> dict:
    return {
        "opcoes": [
            {"os_id": 1, "novo_inicio": novo_inicio.isoformat(), "maquina_id": 1, "justificativa": "x"}
        ],
        "mensagem": "proposta inválida",
    }


def modelos_padrao(**sobrescritos: list) -> dict:
    roteiros = {
        "avaliador": roteiro_avaliador(),
        "agronomico": ["Milho em V6: chuva logo após a aplicação lava o fungicida (bula-abacus-hc, p. 7)."],
        "operacional": ["Pulverizador A livre depois da chuva, a partir das 06:00."],
        # Duas chamadas por proposta (ferramenta + plano); a função decide pelo que já veio na requisição.
        "supervisor": [supervisor_que_escolhe()],
    }
    roteiros.update(sobrescritos)
    return {nome: FakeLlm(roteiro=r) for nome, r in roteiros.items()}


# ---------------------------------------------------------------- ERP


@dataclass
class FakeErp:
    os: dict[int, dict] = field(default_factory=dict)
    talhoes: dict[int, dict] = field(default_factory=dict)
    maquinas: dict[int, dict] = field(default_factory=dict)
    operadores: list[dict] = field(default_factory=list)
    estoque: dict[int, float] = field(default_factory=dict)
    planos_aprovados: Callable[[int], bool] = lambda plano_id: False
    escritas: list[tuple] = field(default_factory=list)
    falhar_em: set[str] = field(default_factory=set)
    indisponivel: bool = False

    def _checar(self):
        if self.indisponivel:
            from agents.clientes.erp import ErpIndisponivel

            raise ErpIndisponivel("fake fora do ar")

    def status(self, os_id: int) -> str:
        return self.os[os_id]["status"]

    def _linha(self, o: dict) -> dict:
        t = self.talhoes[o["talhao_id"]]
        return {**o, "talhao": t["nome"], "area_ha": t["area_ha"]}

    async def listar_os(self, inicio, fim, talhao_id=None):
        self._checar()
        return [
            self._linha(o)
            for o in self.os.values()
            if inicio <= o["inicio_previsto"] <= fim and (talhao_id is None or o["talhao_id"] == talhao_id)
        ]

    async def detalhar_os(self, os_id):
        self._checar()
        return self._linha(self.os[os_id])

    async def consultar_talhao(self, talhao_id):
        self._checar()
        return self.talhoes[talhao_id]

    async def disponibilidade_maquinas(self, inicio, fim, tipo=None):
        self._checar()
        ocupadas = {
            o["maquina_id"]
            for o in self.os.values()
            if o["status"] in ("agendada", "reagendada")
            and o["inicio_previsto"] < fim
            and inicio < o["inicio_previsto"] + timedelta(hours=o["duracao_horas"])
        }
        return [
            m
            for m in self.maquinas.values()
            if m["status"] == "disponivel" and m["id"] not in ocupadas and (tipo is None or m["tipo"] == tipo)
        ]

    async def listar_operadores(self):
        self._checar()
        return self.operadores

    async def suspender_os(self, os_id, incidente_id, motivo):
        self.escritas.append(("suspender_os", os_id))
        if self.os[os_id]["status"] != "agendada":
            return {"ok": False, "erro": f"OS {os_id} não está agendada"}
        self.os[os_id]["status"] = "suspensa_preventiva"
        return {"ok": True, "os": self.os[os_id]}

    async def reagendar_os(self, os_id, novo_inicio, plano_id):
        self.escritas.append(("reagendar_os", os_id, novo_inicio, plano_id))
        if "reagendar_os" in self.falhar_em or not self.planos_aprovados(plano_id):
            return {"ok": False, "erro": "plano não aprovado"}
        if self.os[os_id]["status"] != "suspensa_preventiva":
            return {"ok": False, "erro": "OS não está suspensa"}
        self.os[os_id].update(status="reagendada", inicio_previsto=novo_inicio)
        return {"ok": True}

    async def alocar_maquina(self, os_id, maquina_id, plano_id):
        self.escritas.append(("alocar_maquina", os_id, maquina_id, plano_id))
        if not self.planos_aprovados(plano_id):
            return {"ok": False, "erro": "plano não aprovado"}
        self.os[os_id]["maquina_id"] = maquina_id
        return {"ok": True}

    async def reservar_estoque(self, insumo_id, quantidade, plano_id):
        self.escritas.append(("reservar_estoque", insumo_id, quantidade, plano_id))
        if "reservar_estoque" in self.falhar_em or self.estoque[insumo_id] < quantidade:
            return {"ok": False, "erro": "estoque insuficiente"}
        if not self.planos_aprovados(plano_id):
            return {"ok": False, "erro": "plano não aprovado"}
        self.estoque[insumo_id] -= quantidade
        return {"ok": True}


# ---------------------------------------------------------------- conhecimento, canal, repositório


@dataclass
class FakeConhecimento:
    fatores: dict[tuple[str, str], dict] = field(default_factory=dict)
    trechos: list[dict] = field(default_factory=list)
    buscas: list[tuple] = field(default_factory=list)

    async def buscar_trechos(self, consulta, produto=None, secao=None, limite=3):
        self.buscas.append((consulta, produto, secao))
        return [t for t in self.trechos if produto is None or t.get("produto") == produto][:limite]

    async def fator_perda(self, tipo_insumo, condicao):
        return self.fatores.get((tipo_insumo, condicao))


@dataclass
class FakeCanal:
    alertas: list[str] = field(default_factory=list)
    aprovacoes: list[tuple[int, int, str]] = field(default_factory=list)
    operadores: list[tuple[int, str]] = field(default_factory=list)

    async def enviar_alerta(self, texto):
        self.alertas.append(texto)

    async def pedir_aprovacao(self, incidente_id, plano_id, texto):
        self.aprovacoes.append((incidente_id, plano_id, texto))

    async def notificar_operador(self, chat_id, texto):
        self.operadores.append((chat_id, texto))


@dataclass
class FakeRepo:
    incidentes: dict[int, Incidente] = field(default_factory=dict)
    planos: dict[int, dict] = field(default_factory=dict)
    auditoria: list[dict] = field(default_factory=list)

    def aprovado(self, plano_id: int) -> bool:
        p = self.planos.get(plano_id)
        return bool(p and p["aprovado_por"] and p["respondido_em"])

    def acoes(self) -> list[str]:
        return [f"{a['no']}:{a['acao']}" for a in self.auditoria]

    async def carregar_incidente(self, incidente_id):
        inc = self.incidentes.get(incidente_id)
        return inc.model_copy(deep=True) if inc else None

    async def mudar_status(self, incidente_id, para, de=None):
        inc = self.incidentes.get(incidente_id)
        if inc is None or (de is not None and inc.status not in de):
            return False
        inc.status = para
        return True

    async def criar_plano(self, incidente_id, opcoes_json):
        plano_id = len(self.planos) + 1
        self.planos[plano_id] = {
            "id": plano_id,
            "incidente_id": incidente_id,
            "opcoes_json": opcoes_json,
            "aprovado_por": None,
            "respondido_em": None,
            "criado_em": AGORA,
            "escalado_em": None,
        }
        return plano_id

    async def registrar_resposta(self, plano_id, incidente_id, resposta, respondido_por):
        p = self.planos.get(plano_id)
        if p is None or p["incidente_id"] != incidente_id:
            return "desconhecida"
        if p["respondido_em"] is not None:
            return "repetida"
        p["respondido_em"] = AGORA
        p["aprovado_por"] = respondido_por if resposta == "SIM" else None
        return "ok"

    async def plano_aprovado(self, plano_id):
        return self.aprovado(plano_id)

    async def planos_para_escalar(self, agora, timeout_min):
        return [
            {"plano_id": p["id"], "incidente_id": p["incidente_id"], "criado_em": p["criado_em"]}
            for p in self.planos.values()
            if p["respondido_em"] is None
            and p["escalado_em"] is None
            and p["criado_em"] <= agora - timedelta(minutes=timeout_min)
        ]

    async def marcar_escalado(self, plano_id, agora):
        self.planos[plano_id]["escalado_em"] = agora

    async def auditar(self, incidente_id, no, acao, antes=None, depois=None, justificativa=None):
        self.auditoria.append(
            {
                "incidente_id": incidente_id,
                "no": no,
                "acao": acao,
                "depois": depois,
                "justificativa": justificativa,
            }
        )


# ---------------------------------------------------------------- cenário da demo


def previsao_chuva(agora: datetime = AGORA, total_mm: float = 80, em_horas: int = 36, horas: int = 120):
    por_hora = total_mm / em_horas
    return [
        HoraPrevisao(
            ts=agora + timedelta(hours=i),
            chuva_mm=por_hora if i < em_horas else 0,
            vento_kmh=8,
            umidade_pct=75,
            temp_c=24,
        )
        for i in range(horas)
    ]


def incidente_chuva(incidente_id: int = 7, agora: datetime = AGORA) -> Incidente:
    horas = previsao_chuva(agora)
    return Incidente(
        id=incidente_id,
        talhao_id=1,
        status="aberto",
        gatilhos=[Gatilho(tipo="chuva", valor=80, limiar=20, inicio=agora, fim=agora + timedelta(hours=35))],
        horas=horas,
        criado_em=agora,
    )


def os_abacus(agora: datetime = AGORA, **extra) -> dict:
    os = {
        "id": 1,
        "talhao_id": 1,
        "tipo_operacao": "pulverizacao",
        "insumo_id": 1,
        "produto": PRODUTO,
        "tipo_insumo": "fungicida",
        "dose_por_ha": 0.3,
        "unidade": "L",
        "preco_unitario": 185.0,
        "inicio_previsto": agora + timedelta(hours=20),
        "duracao_horas": 8,
        "maquina_id": 1,
        "operador_id": 1,
        "status": "agendada",
    }
    os.update(extra)
    return os


@dataclass
class Cenario:
    deps: Dependencias
    erp: FakeErp
    repo: FakeRepo
    canal: FakeCanal
    conhecimento: FakeConhecimento


def os_da_fazenda(agora: datetime = AGORA) -> dict[int, dict]:
    return {
        1: os_abacus(agora),
        2: os_abacus(agora, id=2, inicio_previsto=agora + timedelta(days=6)),  # fora da janela de risco
    }


def montar_cenario(
    modelos: dict | None = None,
    settings: Settings | None = None,
    agora: datetime = AGORA,
    incidente_id: int = 7,
) -> Cenario:
    """`modelos=None` usa os LLMs roteirizados; `{}` usa os modelos reais do `Settings` (OpenAI)."""
    repo = FakeRepo()
    repo.incidentes[incidente_id] = incidente_chuva(incidente_id, agora)
    erp = FakeErp(
        os=os_da_fazenda(agora),
        talhoes={
            1: {
                "id": 1,
                "nome": "Talhão 1 - Sede",
                "area_ha": 120.0,
                "cultura": "milho",
                "estagio_fenologico": "V6",
            }
        },
        maquinas={
            1: {"id": 1, "nome": "Pulverizador A", "tipo": "pulverizador", "status": "disponivel"},
            2: {"id": 2, "nome": "Pulverizador B", "tipo": "pulverizador", "status": "disponivel"},
        },
        operadores=[{"id": 1, "nome": "João", "telegram_chat_id": 999}],
        estoque={1: 500.0},  # litros, como no seed
        planos_aprovados=repo.aprovado,
    )
    conhecimento = FakeConhecimento(
        fatores={
            ("fungicida", "chuva"): {
                "fator": 0.5,
                "fonte_ou_estimativa": "estimativa: chuva até 4 h após a aplicação lava parte do produto",
            }
        },
        trechos=[
            {
                "texto": TRECHO_BULA,
                "fonte_doc": "bula-abacus-hc",
                "pagina": 7,
                "secao": "condicoes_climaticas",
                "produto": PRODUTO,
            }
        ],
    )
    canal = FakeCanal()
    deps = Dependencias(
        settings=settings or Settings(_env_file=None),
        erp=erp,
        conhecimento=conhecimento,
        repo=repo,
        canal=canal,
        modelos=modelos_padrao() if modelos is None else modelos,
        relogio=lambda: agora,
    )
    return Cenario(deps=deps, erp=erp, repo=repo, canal=canal, conhecimento=conhecimento)
