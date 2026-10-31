"""FastAPI do serviço `agents` (porta 8000): início de incidente, resposta do gerente e health."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request, status

from agents.canal import CanalLog, criar_canal
from agents.clientes.conhecimento import ConhecimentoHttp
from agents.clientes.erp import ErpMcp
from agents.clientes.repositorio import RepositorioPostgres
from agents.config import Settings
from agents.deps import Dependencias
from agents.modelos import Resposta, RespostaGerente
from agents.runner import Orquestrador, criar_session_service
from agents.timeout import laco_timeouts

logger = logging.getLogger(__name__)


@dataclass
class Servicos:
    orquestrador: Orquestrador
    desligar: Callable[[], Awaitable[None]] | None = None


async def montar_servicos(settings: Settings) -> Servicos:
    """Monta os clientes reais, o orquestrador, o canal (com o callback) e o laço de timeout."""
    conhecimento = ConhecimentoHttp(settings.conhecimento_url)
    deps = Dependencias(
        settings=settings,
        erp=ErpMcp(settings.erp_mcp_url),
        conhecimento=conhecimento,
        repo=RepositorioPostgres.de_url(settings.database_url),
        canal=CanalLog(),
    )
    orquestrador = Orquestrador(deps, criar_session_service(settings.database_url))
    deps.canal = criar_canal(settings.channel, orquestrador.responder)
    await deps.canal.iniciar()
    timeouts = asyncio.create_task(
        laco_timeouts(
            deps.repo,
            lambda: deps.canal,
            deps.relogio,
            settings.approval_timeout_min,
            settings.intervalo_timeout_s,
        )
    )

    async def desligar() -> None:
        timeouts.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await timeouts
        await deps.canal.parar()
        await conhecimento.fechar()
        await deps.repo.engine.dispose()

    return Servicos(orquestrador=orquestrador, desligar=desligar)


class CorpoResposta(RespostaGerente):
    plano_id: int
    respondido_por: str = "painel"


def criar_api(fabrica: Callable[[], Awaitable[Servicos]] | None = None) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        servicos = await (fabrica() if fabrica else montar_servicos(Settings()))
        app.state.servicos = servicos
        yield
        await servicos.orquestrador.aguardar()
        if servicos.desligar:
            await servicos.desligar()

    app = FastAPI(title="TerraMind agents", lifespan=lifespan)

    def orquestrador(request: Request) -> Orquestrador:
        return request.app.state.servicos.orquestrador

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/incidentes/{incidente_id}/iniciar", status_code=status.HTTP_202_ACCEPTED)
    async def iniciar(incidente_id: int, request: Request) -> dict:
        orq = orquestrador(request)
        if await orq.deps.repo.carregar_incidente(incidente_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incidente_inexistente")
        orq.disparar_inicio(incidente_id)
        return {"incidente_id": incidente_id, "status": "aceito"}

    @app.post("/incidentes/{incidente_id}/resposta")
    async def resposta(incidente_id: int, corpo: CorpoResposta, request: Request) -> dict:
        resultado = await orquestrador(request).responder(
            Resposta(incidente_id, corpo.plano_id, corpo.resposta, corpo.respondido_por)
        )
        if resultado == "repetida":
            raise HTTPException(status.HTTP_409_CONFLICT, "resposta_repetida")
        if resultado == "desconhecida":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "plano_desconhecido")
        return {"incidente_id": incidente_id, "plano_id": corpo.plano_id, "status": "registrada"}

    return app


app = criar_api()
