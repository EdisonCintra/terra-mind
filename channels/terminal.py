"""Canal do terminal (`CHANNEL=terminal`): uma conversa com o TerraMind, como a do WhatsApp, no terminal.

O canal guarda a conversa em memória e a expõe pelo `agents` (`GET` e `POST /terminal/mensagens`). Quem
conversa é o cliente `uv run python -m channels.chat`, numa janela própria que fica sempre aberta: alertas e
planos chegam a qualquer momento, e a linha de digitação aceita qualquer mensagem a qualquer hora.

    sim | nao | opcoes          responde o plano pendente mais recente
    sim 12                      responde o plano 12
    pendentes                   lista os planos sem resposta (lidos do banco)
    ajuda                       mostra os comandos
    qualquer outro texto        pergunta ao assistente (OS, incidentes, previsão, estoque, bula...)

Os planos pendentes vêm de `agente.planos` (`respondido_em IS NULL`): um plano criado antes de o processo
reiniciar continua respondível. A conversa em si some quando o `agents` reinicia (só as últimas
`LIMITE_HISTORICO` mensagens ficam). Funciona com o `agents` local ou no Docker.
"""

import asyncio
import contextlib
import itertools
import logging
import unicodedata
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from channels.base import ECO, AoResponder, DecisaoJaRegistrada, NotificationChannel, Resposta

log = logging.getLogger(__name__)

RESPONDIDO_POR = "terminal"
LIMITE_HISTORICO = 500
FUSO = ZoneInfo("America/Sao_Paulo")

# Palavras aceitas para cada decisão, já sem acento e em minúsculas.
DECISOES = {
    "sim": "SIM",
    "s": "SIM",
    "aceitar": "SIM",
    "nao": "NAO",
    "n": "NAO",
    "manter": "NAO",
    "opcoes": "OPCOES",
    "opcao": "OPCOES",
    "o": "OPCOES",
    "proxima": "OPCOES",
}
AJUDA = """sim             aceita a proposta (a OS vai para a nova data)
opcoes          pede outra proposta de data
nao             mantém a data original (a OS volta ao horário de antes, com o risco do clima)
sim <plano_id>  responde um plano específico (sem número: o plano pendente mais recente)
pendentes       lista os planos sem resposta
ajuda           mostra esta ajuda
outro texto     pergunta ao assistente: OS, incidentes, previsão, estoque, bula..."""

De = Literal["terramind", "voce"]
Tipo = Literal["alerta", "aprovacao", "operador", "decisao", "bula", "info", "erro", "mensagem"]

# (plano_id, incidente_id) dos planos sem resposta, do mais antigo para o mais novo.
BuscarPendentes = Callable[[], Awaitable[list[tuple[int, int]]]]


@dataclass
class Mensagem:
    id: int
    de: De
    tipo: Tipo
    texto: str
    titulo: str = ""
    origem: str | None = None  # id da janela de chat que mandou (ela não repete a própria mensagem)
    ts: str = field(default_factory=lambda: datetime.now(FUSO).isoformat(timespec="seconds"))

    def como_json(self) -> dict:
        return asdict(self)


def _pendentes_do_banco() -> list[tuple[int, int]]:
    from erp_mcp.db import conectar

    with conectar() as conn:
        linhas = conn.execute(
            "SELECT id, incidente_id FROM agente.planos WHERE respondido_em IS NULL ORDER BY criado_em, id"
        ).fetchall()
    return [(linha["id"], linha["incidente_id"]) for linha in linhas]


async def buscar_pendentes_no_banco() -> list[tuple[int, int]]:
    return await asyncio.to_thread(_pendentes_do_banco)


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def interpretar(linha: str) -> tuple[str, str | None, int | None]:
    """Classifica a mensagem: ("decisao", SIM|NAO|OPCOES, plano_id|None), ("pendentes"|"ajuda"|"vazio",
    None, None) ou ("pergunta", None, None)."""
    partes = _normalizar(linha).split()
    if not partes:
        return "vazio", None, None
    if partes[0] in ("pendentes", "p", "planos") and len(partes) == 1:
        return "pendentes", None, None
    if partes[0] in ("ajuda", "help", "?") and len(partes) == 1:
        return "ajuda", None, None
    decisao = DECISOES.get(partes[0])
    if decisao and len(partes) == 1:
        return "decisao", decisao, None
    if decisao and len(partes) == 2 and partes[1].lstrip("#").isdecimal():
        return "decisao", decisao, int(partes[1].lstrip("#"))
    return "pergunta", None, None


class TerminalChannel(NotificationChannel):
    def __init__(
        self, ao_responder: AoResponder, *, buscar_pendentes: BuscarPendentes = buscar_pendentes_no_banco
    ):
        self.ao_responder = ao_responder
        self.buscar_pendentes = buscar_pendentes
        self.instancia = uuid.uuid4().hex  # muda quando o agents reinicia: o chat recomeça a contagem
        self.historico: deque[Mensagem] = deque(maxlen=LIMITE_HISTORICO)
        self._ids = itertools.count(1)
        self._nova: asyncio.Condition | None = None
        self._tarefas: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ conversa

    def _condicao(self) -> asyncio.Condition:
        if self._nova is None:  # criada no laço em uso (o canal nasce antes do laço do uvicorn)
            self._nova = asyncio.Condition()
        return self._nova

    async def _publicar(
        self, de: De, tipo: Tipo, texto: str, titulo: str = "", origem: str | None = None
    ) -> Mensagem:
        mensagem = Mensagem(next(self._ids), de, tipo, texto.rstrip(), titulo, origem)
        self.historico.append(mensagem)
        async with self._condicao():
            self._condicao().notify_all()
        return mensagem

    async def mensagens(self, depois: int = 0, espera_s: float = 0) -> list[Mensagem]:
        """As mensagens com id maior que `depois`. Sem nenhuma, espera até `espera_s` (long polling)."""

        def novas() -> list[Mensagem]:
            return [m for m in self.historico if m.id > depois]

        if (achadas := novas()) or espera_s <= 0:
            return achadas
        condicao = self._condicao()
        with contextlib.suppress(TimeoutError):
            async with condicao:
                await asyncio.wait_for(condicao.wait_for(lambda: bool(novas())), espera_s)
        return novas()

    async def receber(self, texto: str, origem: str | None = None) -> Mensagem:
        """Mensagem do gerente. Fica na conversa na hora; a resposta chega depois, como no WhatsApp."""
        mensagem = await self._publicar("voce", "mensagem", texto.strip(), origem=origem)
        tarefa = asyncio.create_task(self.processar(texto))
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)
        return mensagem

    # ------------------------------------------------- o que o agents manda ao gerente

    async def enviar_alerta(self, texto: str, incidente_id: int | None = None) -> None:
        log.info("[terminal] alerta: %s", texto)
        titulo = "Alerta" + (f" · incidente {incidente_id}" if incidente_id else "")
        await self._publicar("terramind", "alerta", texto, titulo)

    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None:
        log.info("[terminal] aprovação pendente: incidente %s, plano %s", incidente_id, plano_id)
        await self._publicar(
            "terramind",
            "aprovacao",
            f"{texto}\n\nResponda: sim = aceitar proposta · opcoes = ver próxima opção · "
            f"nao = manter data original (com o risco)   (ou: sim {plano_id})",
            f"Aprovação · incidente {incidente_id} · plano {plano_id}",
        )

    async def notificar_operador(self, chat_id: int, texto: str) -> None:
        log.info("[terminal] aviso ao operador %s: %s", chat_id, texto)
        await self._publicar("terramind", "operador", texto, f"Aviso ao operador {chat_id}")

    # ------------------------------------------------- o que o gerente manda ao agents

    async def processar(self, texto: str) -> None:
        """Trata uma mensagem do gerente. Nunca levanta: o erro vira mensagem na conversa."""
        try:
            tipo, decisao, plano_id = interpretar(texto)
            if tipo == "vazio":
                return
            if tipo == "ajuda":
                await self._publicar("terramind", "info", AJUDA, "Comandos")
            elif tipo == "pendentes":
                await self._listar_pendentes()
            elif tipo == "decisao":
                await self._decidir(decisao, plano_id)
            else:
                await self._perguntar(texto.strip())
        except Exception as e:  # noqa: BLE001
            log.exception("canal terminal: falha ao tratar %r", texto)
            await self._publicar("terramind", "erro", f"{type(e).__name__}: {e}", "Erro")

    async def _listar_pendentes(self) -> None:
        pendentes = await self.buscar_pendentes()
        if not pendentes:
            await self._publicar("terramind", "info", "Nenhum plano esperando resposta.", "Pendentes")
            return
        linhas = "\n".join(f"plano {p} · incidente {i}" for p, i in pendentes)
        await self._publicar("terramind", "info", linhas, "Pendentes")

    async def _decidir(self, decisao: str, plano_id: int | None) -> None:
        pendentes = await self.buscar_pendentes()
        if plano_id is None:
            if not pendentes:
                await self._publicar("terramind", "info", "Nenhum plano esperando resposta.")
                return
            plano_id, incidente_id = pendentes[-1]
        else:
            incidente_id = dict(pendentes).get(plano_id)
            if incidente_id is None:
                aviso = f"O plano {plano_id} não existe ou já foi respondido."
                await self._publicar("terramind", "info", aviso)
                return
        try:
            await self.ao_responder(Resposta(incidente_id, plano_id, decisao, RESPONDIDO_POR))
        except DecisaoJaRegistrada:
            aviso = f"Decisão já registrada para o plano {plano_id}. Nada foi alterado."
            await self._publicar("terramind", "info", aviso)
            return
        await self._publicar(
            "terramind", "decisao", f"{ECO[decisao]} · plano {plano_id} · incidente {incidente_id}"
        )

    async def _perguntar(self, pergunta: str) -> None:
        if self.ao_perguntar is None:
            await self._publicar("terramind", "info", AJUDA, "Não entendi")
            return
        resposta = await self.ao_perguntar(pergunta, RESPONDIDO_POR, contato="terminal")
        await self._publicar("terramind", "bula", resposta, "Resposta")

    # --------------------------------------------------------------- ciclo de vida

    async def iniciar(self) -> None:
        log.info("canal terminal ativo: abra o chat com `uv run python -m channels.chat`")
        await self._publicar("terramind", "info", AJUDA, "TerraMind conectado")

    async def parar(self) -> None:
        for tarefa in list(self._tarefas):
            tarefa.cancel()
        await asyncio.gather(*self._tarefas, return_exceptions=True)
