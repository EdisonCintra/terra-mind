"""Abrir ordem de serviço pelo chat: o Agente Operacional monta, o código confere e o gerente confirma.

Fluxo (assistente do chat, `agents.assistente`):
1. O gerente pede ("abre uma pulverização de Abacus no Talhão 1 amanhã cedo"). O assistente chama
   `abrir_ordem_servico` com o pedido completo.
2. `AberturaOS.propor` roda o Agente Operacional (`criar_para_nova_os`), que olha os insumos (estoque), as
   máquinas livres no horário e os operadores, e devolve uma `PropostaOS`.
3. O código confere a proposta contra os dados (`conferir`): talhão, produto, máquina e operador do escopo
   de quem pede e da mesma fazenda; estoque para área × dose; início no futuro, entre 06:00 e 18:00; máquina
   livre no horário. Recusada, o Operacional tenta mais uma vez com o motivo.
4. A proposta conferida fica pendente na conversa e o assistente mostra ao gerente.
5. Só numa mensagem SEGUINTE do gerente, com uma confirmação explícita ("confirmo"), `confirmar_ordem_servico`
   confere de novo e grava pelo MCP (`abrir_os`), que repete as regras no SQL e audita com o nó `assistente`.

Garantias em código, não no prompt: o LLM não grava sem uma proposta conferida, não confirma na mesma
mensagem em que propôs, e não confirma sem o gerente ter escrito a confirmação. Usa "confirmo" e não "sim"
porque "sim" já é a resposta de plano no WhatsApp quando há um plano esperando.
"""

import json
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timedelta
from typing import Any

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import ValidationError

from agents import observabilidade as obs
from agents.agentes._comum import ENTRADA_LLM, chave_falha
from agents.agentes.operacional.agent import NOME_NOVA_OS, criar_para_nova_os
from agents.clientes.consultas import ConsultasPainel, Escopo
from agents.clientes.erp import ErpIndisponivel
from agents.deps import Dependencias
from agents.modelos import PropostaOS
from agents.rules.janelas import FUSO

logger = logging.getLogger(__name__)

APP_NAME = "abertura_os"
TIPO_MAQUINA = "pulverizador"
HORA_MIN, HORA_MAX = 6, 18  # a mesma faixa de início do Validador do plano
MAX_DURACAO_H = 24
TENTATIVAS = 2
CHAVE_PENDENTE = "os_pendente"
COMO_CONFIRMAR = "Para abrir a OS, responda CONFIRMO. Para mudar algo, diga o que mudar."

_CONFIRMA = re.compile(r"\b(confirm\w*|pode abrir|pode criar|abre|abra|abrir)\b")
_NEGA = re.compile(r"\b(nao|cancela\w*|espera|calma)\b")


def _normalizar(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().casefold().strip()


def confirmou(texto: str | None) -> bool:
    """O gerente escreveu uma confirmação explícita, sem negar ("confirmo", "pode abrir"; não "não abra")."""
    limpo = _normalizar(texto or "")
    return bool(_CONFIRMA.search(limpo)) and not _NEGA.search(limpo)


def _escopo(tool_context: ToolContext) -> Escopo:
    bruto = tool_context.state.get("escopo") or {}
    return Escopo(tuple(bruto.get("fazendas") or ()), bool(bruto.get("todas")))


def _texto_do_usuario(tool_context: ToolContext) -> str:
    conteudo = tool_context.user_content
    return " ".join(p.text for p in (conteudo.parts if conteudo else None) or [] if p.text)


def _ids(linhas: list[dict]) -> dict[int, dict]:
    return {int(li["id"]): li for li in linhas}


def _hora(dt: datetime) -> str:
    return f"{dt.astimezone(FUSO):%d/%m %H:%M}"


class AberturaOS:
    """O Agente Operacional no modo "nova OS", com as ferramentas presas ao escopo de quem pede."""

    def __init__(self, deps: Dependencias, consultas: ConsultasPainel, leitura: dict[str, Any]):
        self.deps = deps
        self.consultas = consultas
        ferramentas = [
            leitura["listar_talhoes"],
            leitura["estoque_insumos"],
            leitura["listar_ordens"],
            leitura["janelas_de_aplicacao"],
            leitura["perguntar_a_bula"],
            self._criar_maquinas_livres(),
            self._criar_operadores_do_talhao(),
        ]
        self.sessoes = InMemorySessionService()
        self.runner = Runner(
            app_name=APP_NAME,
            agent=criar_para_nova_os(deps, ferramentas),
            session_service=self.sessoes,
        )

    # ------------------------------------------------------------------- ferramentas do Operacional

    async def _talhao_no_escopo(self, escopo: Escopo, talhao_id: int) -> dict | None:
        return _ids(await self.consultas.talhoes(escopo)).get(talhao_id)

    def _criar_maquinas_livres(self):
        async def maquinas_livres(
            tool_context: ToolContext, talhao_id: int, inicio: str, duracao_horas: float = 8
        ) -> dict:
            """Pulverizadores da fazenda do talhão livres (sem outra OS) no horário pedido.

            Args:
                talhao_id: o talhão da OS (veja listar_talhoes).
                inicio: ISO 8601 com fuso, ex.: 2026-11-04T07:00:00-03:00.
                duracao_horas: duração da OS.
            """
            if await self._talhao_no_escopo(_escopo(tool_context), talhao_id) is None:
                return {"ok": False, "erro": f"talhão {talhao_id} não encontrado"}
            ini = datetime.fromisoformat(inicio)
            ini = ini if ini.tzinfo else ini.replace(tzinfo=FUSO)
            maquinas = await self.deps.erp.disponibilidade_maquinas(
                ini, ini + timedelta(hours=duracao_horas), TIPO_MAQUINA, talhao_id
            )
            return {"ok": True, "maquinas": [{"id": m["id"], "nome": m["nome"]} for m in maquinas]}

        return maquinas_livres

    def _criar_operadores_do_talhao(self):
        async def operadores_do_talhao(tool_context: ToolContext, talhao_id: int) -> dict:
            """Operadores da fazenda do talhão: id e nome."""
            if await self._talhao_no_escopo(_escopo(tool_context), talhao_id) is None:
                return {"ok": False, "erro": f"talhão {talhao_id} não encontrado"}
            operadores = await self.deps.erp.listar_operadores(talhao_id)
            return {"ok": True, "operadores": [{"id": o["id"], "nome": o["nome"]} for o in operadores]}

        return operadores_do_talhao

    # ------------------------------------------------------------------------------- conferência

    async def conferir(self, proposta: PropostaOS, escopo: Escopo) -> tuple[dict | None, str | None]:
        """Confere a proposta contra os dados. Devolve `(resumo para o gerente, None)` ou `(None, motivo)`."""
        p = proposta
        obrigatorios = ("talhao_id", "insumo_id", "dose_por_ha", "inicio", "duracao_horas", "maquina_id")
        if faltando := [c for c in obrigatorios if getattr(p, c) is None]:
            return None, f"faltam campos: {', '.join(faltando)}"
        talhao = await self._talhao_no_escopo(escopo, p.talhao_id)
        if talhao is None:
            return None, f"talhão {p.talhao_id} não é das fazendas de quem pediu"
        insumo = _ids(await self.consultas.insumos(escopo)).get(p.insumo_id)
        if insumo is None:
            return None, f"insumo {p.insumo_id} não está no estoque das fazendas de quem pediu"
        if talhao.get("fazenda_id") is not None and insumo.get("fazenda_id") != talhao["fazenda_id"]:
            return None, f"{insumo['produto']} não é do estoque da fazenda do {talhao['nome']}"
        if p.dose_por_ha <= 0 or not 0 < p.duracao_horas <= MAX_DURACAO_H:
            return None, f"dose precisa ser > 0 e duração entre 0 e {MAX_DURACAO_H} h"
        necessario = float(talhao["area_ha"]) * p.dose_por_ha
        estoque = float(insumo["estoque"])
        if estoque < necessario:
            return None, (
                f"estoque de {insumo['produto']} insuficiente: a OS pede {necessario:.1f} {insumo['unidade']}"
                f" e há {estoque:.1f}"
            )
        local = p.inicio.astimezone(FUSO)
        if p.inicio <= self.deps.relogio():
            return None, f"o início {_hora(p.inicio)} já passou"
        if not HORA_MIN <= local.hour + local.minute / 60 <= HORA_MAX:
            return None, f"o início {_hora(p.inicio)} está fora de {HORA_MIN:02d}:00–{HORA_MAX:02d}:00"
        fim = p.inicio + timedelta(hours=p.duracao_horas)
        livres = _ids(await self.deps.erp.disponibilidade_maquinas(p.inicio, fim, TIPO_MAQUINA, p.talhao_id))
        if p.maquina_id not in livres:
            return None, f"a máquina {p.maquina_id} não está livre de {_hora(p.inicio)} a {_hora(fim)}"
        operador = None
        if p.operador_id is not None:
            operador = _ids(await self.deps.erp.listar_operadores(p.talhao_id)).get(p.operador_id)
            if operador is None:
                return None, f"o operador {p.operador_id} não é da fazenda do {talhao['nome']}"
        livre = estoque - float(insumo.get("comprometido") or 0)
        resumo = {
            "talhao": talhao["nome"],
            "area_ha": float(talhao["area_ha"]),
            "produto": insumo["produto"],
            "dose": f"{p.dose_por_ha:g} {insumo['unidade']}/ha",
            "quantidade_total": f"{necessario:.1f} {insumo['unidade']}",
            "estoque": f"{estoque:.1f} {insumo['unidade']}",
            "inicio": _hora(p.inicio),
            "fim": _hora(fim),
            "maquina": livres[p.maquina_id]["nome"],
            "operador": operador["nome"] if operador else "sem operador",
            "justificativa": p.justificativa,
        }
        if livre < necessario:
            resumo["aviso_estoque"] = (
                f"descontadas as OS já agendadas, sobram {livre:.1f} {insumo['unidade']}: comprar ou remarcar"
            )
        return resumo, None

    # ----------------------------------------------------------------------------- Operacional

    async def _rodar(self, pedido: str, escopo: Escopo, quem: str, motivo: str | None) -> PropostaOS | None:
        sid = f"os-{uuid.uuid4().hex[:12]}"
        estado = {
            "pedido": pedido,
            "quem": quem,
            "escopo": {"fazendas": list(escopo.fazendas), "todas": escopo.todas},
            "motivo_recusa": motivo,
        }
        await self.sessoes.create_session(app_name=APP_NAME, user_id=quem, session_id=sid, state=estado)
        try:
            mensagem = types.Content(role="user", parts=[types.Part(text=ENTRADA_LLM)])
            async for _ in self.runner.run_async(user_id=quem, session_id=sid, new_message=mensagem):
                pass
            sessao = await self.sessoes.get_session(app_name=APP_NAME, user_id=quem, session_id=sid)
            estado = sessao.state if sessao else {}
        finally:
            await self.sessoes.delete_session(app_name=APP_NAME, user_id=quem, session_id=sid)
        if estado.get(chave_falha(NOME_NOVA_OS)):
            return None
        bruto = estado.get("proposta_os")
        try:
            if isinstance(bruto, str):
                bruto = json.loads(bruto)
            return PropostaOS.model_validate(bruto) if bruto else None
        except (ValueError, ValidationError):
            logger.warning("abertura_os: proposta fora do schema: %r", bruto)
            return None

    async def propor(self, pedido: str, escopo: Escopo, quem: str) -> dict:
        """Roda o Operacional e confere. `{"ok": True, "proposta": ..., "resumo": ...}`, `falta` ou `erro`."""
        motivo = None
        with obs.etapa("abertura-os", entrada={"pedido": pedido}) as passo:
            for _ in range(TENTATIVAS):
                proposta = await self._rodar(pedido, escopo, quem, motivo)
                if proposta is None:
                    passo.sinalizar("ERROR", "operacional sem proposta")
                    return {"ok": False, "erro": "o Agente Operacional não conseguiu montar a OS agora"}
                if not proposta.pronta:
                    passo.saida({"falta": proposta.falta})
                    return {
                        "ok": False,
                        "falta": proposta.falta or ["faltam dados para montar a OS"],
                        "justificativa": proposta.justificativa,
                    }
                try:
                    resumo, motivo = await self.conferir(proposta, escopo)
                except ErpIndisponivel as e:
                    return {"ok": False, "erro": f"ERP indisponível: {e}"}
                if resumo is not None:
                    passo.saida({"proposta": proposta.model_dump(mode="json"), "resumo": resumo})
                    return {"ok": True, "proposta": proposta.model_dump(mode="json"), "resumo": resumo}
                logger.info("abertura_os: proposta recusada na conferência: %s", motivo)
            passo.sinalizar("WARNING", f"proposta recusada: {motivo}")
        return {"ok": False, "erro": f"não consegui montar uma OS válida: {motivo}"}

    async def abrir(self, proposta: PropostaOS, escopo: Escopo, pedido_por: str) -> dict:
        """Confere de novo (o estoque e as máquinas podem ter mudado) e grava pelo MCP."""
        try:
            resumo, motivo = await self.conferir(proposta, escopo)
        except ErpIndisponivel as e:
            return {"ok": False, "erro": f"ERP indisponível: {e}"}
        if resumo is None:
            return {"ok": False, "erro": f"a proposta não vale mais: {motivo}"}
        r = await self.deps.erp.abrir_os(
            proposta.talhao_id,
            proposta.insumo_id,
            proposta.dose_por_ha,
            proposta.inicio,
            proposta.duracao_horas,
            proposta.maquina_id,
            proposta.operador_id,
            pedido_por,
        )
        if not r.get("ok"):
            return {"ok": False, "erro": r.get("erro") or "o ERP recusou a OS"}
        return {"ok": True, "os_id": (r.get("os") or {}).get("id"), "resumo": resumo}


# ------------------------------------------------------------------------- ferramentas do assistente


def criar_ferramentas_os(abertura: AberturaOS) -> list:
    """`abrir_ordem_servico` (propõe) e `confirmar_ordem_servico` (grava) para o assistente do chat."""

    async def abrir_ordem_servico(tool_context: ToolContext, pedido: str) -> dict:
        """Monta uma OS de pulverização nova com o Agente Operacional (estoque, máquina livre e operador) e
        a deixa esperando a confirmação do gerente. NÃO grava nada.

        Args:
            pedido: o pedido completo do gerente, juntando o que ele disse na conversa: talhão, produto,
                dose, dia e hora, duração, máquina e operador (o que ele tiver dito; o resto o Operacional
                escolhe).

        Returns:
            {"ok": true, "resumo": {...}, "como_confirmar": ...}: mostre o resumo e peça a confirmação.
            {"ok": false, "falta": [...]}: pergunte ao gerente o que falta.
            {"ok": false, "erro": ...}: explique o erro.
        """
        escopo = _escopo(tool_context)
        if escopo.vazio:
            return {"ok": False, "erro": "este contato não é gerente de nenhuma fazenda"}
        tool_context.state[CHAVE_PENDENTE] = None  # um pedido novo descarta a proposta anterior
        r = await abertura.propor(pedido, escopo, str(tool_context.state.get("quem") or tool_context.user_id))
        if not r["ok"]:
            return r
        tool_context.state[CHAVE_PENDENTE] = {
            "proposta": r["proposta"],
            "invocacao": tool_context.invocation_id,
        }
        return {"ok": True, "resumo": r["resumo"], "como_confirmar": COMO_CONFIRMAR}

    async def confirmar_ordem_servico(tool_context: ToolContext) -> dict:
        """Abre no ERP a OS proposta por abrir_ordem_servico. Chame só quando o gerente, depois de ver a
        proposta, responder que confirma (ex.: "confirmo")."""
        pendente = tool_context.state.get(CHAVE_PENDENTE)
        if not pendente:
            return {"ok": False, "erro": "não há OS proposta nesta conversa; use abrir_ordem_servico antes"}
        if pendente["invocacao"] == tool_context.invocation_id:
            return {
                "ok": False,
                "erro": "mostre a proposta ao gerente e espere a resposta dele: " + COMO_CONFIRMAR,
            }
        if not confirmou(_texto_do_usuario(tool_context)):
            return {"ok": False, "erro": "o gerente não confirmou. " + COMO_CONFIRMAR}
        proposta = PropostaOS.model_validate(pendente["proposta"])
        tool_context.state[CHAVE_PENDENTE] = None  # uma confirmação, uma tentativa de gravar
        quem = tool_context.state.get("quem") or "gerente"
        r = await abertura.abrir(proposta, _escopo(tool_context), f"{quem} ({tool_context.user_id})")
        if r["ok"]:
            obs.sinalizar("DEFAULT", f"OS {r['os_id']} aberta pelo chat")
        return r

    return [abrir_ordem_servico, confirmar_ordem_servico]
