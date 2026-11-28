"""Abre uma sessão por incidente e a retoma quando o gerente responde (decisão A2, contrato 9)."""

import asyncio
import logging
from collections import defaultdict

from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, DatabaseSessionService
from google.genai import types

from agents import observabilidade as obs
from agents.clientes.repositorio import ResultadoResposta, url_asyncpg
from agents.deps import Dependencias
from agents.nodes.aprovacao import interrupt_id
from agents.workflow import APP_NAME, criar_app
from channels.base import Resposta

logger = logging.getLogger(__name__)

USER_ID = "terramind"
REQUEST_INPUT = "adk_request_input"


def session_id(incidente_id: int) -> str:
    return f"incidente-{incidente_id}"


def criar_session_service(database_url: str) -> DatabaseSessionService:
    """Sessões do ADK no schema `agente` do mesmo Postgres (o ADK cria as próprias tabelas)."""
    url = url_asyncpg(database_url)
    if url.startswith("postgresql+asyncpg://"):
        return DatabaseSessionService(db_url=url, connect_args={"server_settings": {"search_path": "agente"}})
    return DatabaseSessionService(db_url=url)


def mensagem_de_resposta(r: Resposta) -> types.Content:
    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=interrupt_id(r.plano_id),
                    name=REQUEST_INPUT,
                    response={"resposta": r.resposta, "respondido_por": r.respondido_por},
                )
            )
        ],
    )


class Orquestrador:
    def __init__(self, deps: Dependencias, session_service: BaseSessionService):
        self.deps = deps
        self.session_service = session_service
        self.runner = Runner(app=criar_app(deps), session_service=session_service)
        self._travas: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._tarefas: set[asyncio.Task] = set()

    async def iniciar(self, incidente_id: int) -> bool:
        """Roda o incidente até a pausa da aprovação ou até o fim. `False` se já tinha sido iniciado."""
        async with self._travas[incidente_id]:
            sid = session_id(incidente_id)
            existente = await self.session_service.get_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=sid
            )
            if existente is not None:
                return False
            await self.session_service.create_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=sid, state={"incidente_id": incidente_id}
            )
            texto = types.Content(role="user", parts=[types.Part(text=f"Incidente {incidente_id}")])
            with obs.rastrear(
                obs.TRACE_AVALIAR_INCIDENTE,
                entrada={"incidente_id": incidente_id},
                sessao=sid,
                usuario=USER_ID,
                tags=["incidente"],
                metadados={"incidente_id": incidente_id},
            ) as rastro:
                await self._rodar(incidente_id, texto, rastro)
        return True

    async def responder(self, r: Resposta, esperar: bool = False) -> ResultadoResposta:
        """Grava a resposta (idempotente) e retoma a sessão.

        Resposta já gravada só retoma de novo se a sessão ainda estiver pausada nesse plano (a
        retomada anterior falhou ou o processo reiniciou) e se ela bater com a gravada; sem isso, um
        reenvio do painel ou do Telegram nunca destravaria o incidente.
        """
        resultado = await self.deps.repo.registrar_resposta(
            r.plano_id, r.incidente_id, r.resposta, r.respondido_por
        )
        if resultado == "desconhecida":
            return resultado
        if resultado == "repetida":
            aprovado = await self.deps.repo.plano_aprovado(r.plano_id)
            if (r.resposta == "SIM") != aprovado or not await self._aguardando(r):
                return resultado
            logger.warning("incidente %s: retomando o plano %s de novo", r.incidente_id, r.plano_id)
        if esperar:
            await self._retomar(r)
        else:
            self._em_segundo_plano(self._retomar(r))
        return "ok"

    def disparar_inicio(self, incidente_id: int) -> None:
        self._em_segundo_plano(self.iniciar(incidente_id))

    async def aguardar(self) -> None:
        """Espera as execuções em segundo plano (usado nos testes e no desligamento)."""
        while self._tarefas:
            await asyncio.gather(*list(self._tarefas), return_exceptions=True)

    async def _retomar(self, r: Resposta) -> None:
        async with self._travas[r.incidente_id]:
            # Conferido de novo dentro da trava: dois reenvios simultâneos retomam uma vez só.
            if not await self._aguardando(r):
                logger.info("incidente %s: plano %s não está mais pendente", r.incidente_id, r.plano_id)
                return
            with obs.rastrear(
                obs.TRACE_RETOMAR_INCIDENTE,
                entrada={"plano_id": r.plano_id, "resposta": r.resposta, "respondido_por": r.respondido_por},
                sessao=session_id(r.incidente_id),
                usuario=r.respondido_por,
                tags=["incidente", "aprovacao"],
                metadados={"incidente_id": r.incidente_id, "plano_id": r.plano_id, "resposta": r.resposta},
            ) as rastro:
                rastro.pontuar(
                    obs.SCORE_DECISAO_GERENTE,
                    r.resposta,
                    "CATEGORICAL",
                    f"plano {r.plano_id}, respondido por {r.respondido_por}",
                )
                await self._rodar(r.incidente_id, mensagem_de_resposta(r), rastro)

    async def _aguardando(self, r: Resposta) -> bool:
        """A sessão está pausada no pedido de aprovação deste plano (chamada sem resposta)?"""
        sessao = await self.session_service.get_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=session_id(r.incidente_id)
        )
        if sessao is None:
            return False
        alvo = interrupt_id(r.plano_id)
        chamado = respondido = False
        for evento in sessao.events:
            chamado = chamado or any(fc.id == alvo for fc in evento.get_function_calls())
            respondido = respondido or any(fr.id == alvo for fr in evento.get_function_responses())
        return chamado and not respondido

    def _em_segundo_plano(self, corrotina) -> None:
        tarefa = asyncio.create_task(corrotina)
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)

    async def _rodar(self, incidente_id: int, mensagem: types.Content, rastro: obs.Rastro) -> None:
        erros: list[str] = []
        try:
            async for evento in self.runner.run_async(
                user_id=USER_ID, session_id=session_id(incidente_id), new_message=mensagem
            ):
                if evento.error_code:
                    erros.append(f"{evento.author}: {evento.error_code}")
                    logger.error("incidente %s: %s %s", incidente_id, evento.error_code, evento.error_message)
        except Exception as e:  # noqa: BLE001 - erro interno não pode deixar o incidente sem dono
            logger.exception("incidente %s: o workflow falhou", incidente_id)
            rastro.sinalizar("ERROR", f"workflow falhou: {type(e).__name__}: {e}")
            await self._falha_interna(incidente_id)
        if erros:
            rastro.sinalizar("WARNING", "; ".join(erros))
        if rastro.ativo:
            await self._registrar_desfecho(incidente_id, rastro, erros)

    async def _registrar_desfecho(self, incidente_id: int, rastro: obs.Rastro, erros: list[str]) -> None:
        """Saída do trace: onde o incidente parou e por quê. Só roda com o Langfuse ativo."""
        try:
            incidente = await self.deps.repo.carregar_incidente(incidente_id)
            sessao = await self.session_service.get_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=session_id(incidente_id)
            )
        except Exception:  # noqa: BLE001 - observabilidade nunca derruba o fluxo
            logger.debug("incidente %s: não consegui ler o desfecho", incidente_id, exc_info=True)
            return
        estado = sessao.state if sessao else {}
        status_final = incidente.status if incidente else "desconhecido"
        motivo = (estado.get("motivo_manual") or {}).get("codigo") or estado.get("motivo_encerramento")
        aguardando = status_final == "aguardando_aprovacao"
        rastro.saida(
            {
                "status": status_final,
                "motivo": motivo,
                "os_em_risco": [o.get("os_id") for o in estado.get("os_em_risco") or []],
                "os_suspensas": estado.get("os_suspensas") or [],
                "plano_id": estado.get("plano_id"),
                "mensagem_ao_gerente": estado.get("mensagem") if aguardando else None,
                "erros": erros,
            }
        )
        rastro.pontuar(obs.SCORE_DESFECHO_INCIDENTE, status_final, "CATEGORICAL", motivo)

    async def _falha_interna(self, incidente_id: int) -> None:
        try:
            await self.deps.repo.mudar_status(
                incidente_id, "aguardando_decisao_manual", de={"aberto", "aguardando_aprovacao"}
            )
            await self.deps.repo.auditar(incidente_id, "runner", "falha_interna")
            await self.deps.canal.enviar_alerta(
                f"Incidente {incidente_id}: falha interna no TerraMind. Verifique as OS no painel.",
                incidente_id=incidente_id,
            )
        except Exception:  # noqa: BLE001
            logger.exception("incidente %s: falha ao registrar a falha interna", incidente_id)
