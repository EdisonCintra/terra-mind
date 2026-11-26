"""FastAPI do serviço `agents` (porta 8000): incidente, resposta do gerente, Pergunte à bula,
agenda e janelas (contrato 24), limites da bula (contrato 26) e relatório de aplicação (G7)."""

import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import date

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agents.agenda import AgendaIndisponivel, NaoEncontrado, PedidoJanela, ServicoAgenda
from agents.bula import PergunteBula
from agents.canal import CanalLog, criar_canal
from agents.clientes.clima import OpenMeteoClima
from agents.clientes.conhecimento import ConhecimentoHttp
from agents.clientes.erp import ErpMcp
from agents.clientes.repositorio import RepositorioPostgres
from agents.config import Settings
from agents.deps import Dependencias
from agents.limites import ExtratorLimites
from agents.modelos import RespostaGerente
from agents.relatorio import RelatorioIndisponivel, ServicoRelatorio
from agents.runner import Orquestrador, criar_session_service
from agents.timeout import laco_timeouts
from channels.base import Resposta

logger = logging.getLogger(__name__)


@dataclass
class Servicos:
    orquestrador: Orquestrador
    desligar: Callable[[], Awaitable[None]] | None = None
    bula: PergunteBula | None = None  # G4
    agenda: ServicoAgenda | None = None  # G2 e G3
    limites: ExtratorLimites | None = None  # contrato 26
    relatorio: ServicoRelatorio | None = None  # G7


async def montar_servicos(settings: Settings) -> Servicos:
    """Monta os clientes reais, o orquestrador, o canal (com o callback) e o laço de timeout."""
    conhecimento = ConhecimentoHttp(settings.conhecimento_url)
    clima = OpenMeteoClima(settings.open_meteo_url)
    deps = Dependencias(
        settings=settings,
        erp=ErpMcp(settings.erp_mcp_url),
        conhecimento=conhecimento,
        repo=RepositorioPostgres.de_url(settings.database_url),
        canal=CanalLog(),
        clima=clima,
    )
    orquestrador = Orquestrador(deps, criar_session_service(settings.database_url))
    bula = PergunteBula(deps)
    deps.canal = criar_canal(settings.channel, orquestrador.responder)
    deps.canal.definir_ao_perguntar(bula.responder_no_canal)  # G4: pergunta livre no Telegram
    await deps.canal.iniciar()
    timeouts = asyncio.create_task(
        laco_timeouts(
            deps.repo,
            deps.canal,
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
        await clima.fechar()
        await deps.repo.engine.dispose()

    return Servicos(
        orquestrador=orquestrador,
        desligar=desligar,
        bula=bula,
        agenda=ServicoAgenda(deps),
        limites=ExtratorLimites(deps),
        relatorio=ServicoRelatorio(deps),
    )


class CorpoResposta(RespostaGerente):
    plano_id: int
    respondido_por: str = "painel"


class CorpoPergunta(BaseModel):
    pergunta: str = Field(min_length=3, max_length=500)
    produto: str | None = None


class CorpoLimites(BaseModel):
    produto: str = Field(min_length=2, max_length=120)


def _servico(request: Request, nome: str):
    servico = getattr(request.app.state.servicos, nome, None)
    if servico is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{nome}_indisponivel")
    return servico


def conferir_token(request: Request, x_agents_token: str | None = Header(None)) -> None:
    """Contrato 23: com `AGENTS_TOKEN` configurado, só o painel (que sabe o segredo) responde um plano.
    Só esta rota exige: o webhook do WhatsApp usa a assinatura do Twilio; o Monitor chama /iniciar."""
    settings = getattr(getattr(request.app.state.servicos.orquestrador, "deps", None), "settings", None)
    esperado = getattr(settings, "agents_token", None)
    if esperado and not hmac.compare_digest(x_agents_token or "", esperado):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_invalido")


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

    def canal_whatsapp(request: Request):
        """O canal em uso, se for o WhatsApp (F4); com outro canal as rotas do webhook não existem."""
        canal = getattr(request.app.state.servicos.orquestrador.deps, "canal", None)
        if not hasattr(canal, "assinatura_valida"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "canal_whatsapp_inativo")
        return canal

    def url_do_webhook(request: Request) -> str:
        """URL que o Twilio chamou, para a conta da assinatura: atrás de túnel ou proxy o `agents` vê http
        e o host interno, então vale o que o proxy informa em `X-Forwarded-*` (ou `TWILIO_WEBHOOK_URL`)."""
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
        consulta = f"?{request.url.query}" if request.url.query else ""
        return f"{proto}://{host.split(',')[0].strip()}{request.url.path}{consulta}"

    @app.post("/whatsapp/webhook", include_in_schema=False)
    async def whatsapp_receber(request: Request) -> Response:
        """Mensagens e cliques do WhatsApp (Twilio): só vale com `X-Twilio-Signature`; roda em 2º plano."""
        canal = canal_whatsapp(request)
        formulario = await request.form()
        parametros = [(k, v) for k, v in formulario.multi_items() if isinstance(v, str)]
        url = canal.url_assinada(url_do_webhook(request))
        if not canal.assinatura_valida(url, parametros, request.headers.get("X-Twilio-Signature")):
            logger.warning("webhook do WhatsApp com assinatura inválida (URL conferida: %s)", url)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "assinatura_invalida")
        canal.agendar(dict(parametros))
        return Response(
            "<Response/>", media_type="text/xml"
        )  # o Twilio espera TwiML; vazio = nada a responder

    @app.post("/incidentes/{incidente_id}/iniciar", status_code=status.HTTP_202_ACCEPTED)
    async def iniciar(incidente_id: int, request: Request) -> dict:
        orq = orquestrador(request)
        if await orq.deps.repo.carregar_incidente(incidente_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "incidente_inexistente")
        orq.disparar_inicio(incidente_id)
        return {"incidente_id": incidente_id, "status": "aceito"}

    @app.post("/incidentes/{incidente_id}/resposta", dependencies=[Depends(conferir_token)])
    async def resposta(incidente_id: int, corpo: CorpoResposta, request: Request) -> dict:
        resultado = await orquestrador(request).responder(
            Resposta(incidente_id, corpo.plano_id, corpo.resposta, corpo.respondido_por)
        )
        if resultado == "repetida":
            raise HTTPException(status.HTTP_409_CONFLICT, "resposta_repetida")
        if resultado == "desconhecida":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "plano_desconhecido")
        return {"incidente_id": incidente_id, "plano_id": corpo.plano_id, "status": "registrada"}

    @app.post("/bula/perguntar")
    async def perguntar_a_bula(corpo: CorpoPergunta, request: Request) -> dict:
        """G4: resposta com trecho, fonte e página, conferida em código.

        Sem trecho conferido, a resposta é "A bula não traz isso."
        """
        bula = request.app.state.servicos.bula
        if bula is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "pergunte_bula_indisponivel")
        resultado = await bula.perguntar(corpo.pergunta.strip(), (corpo.produto or "").strip() or None)
        return resultado.model_dump(mode="json")

    @app.get("/agenda")
    async def agenda(
        request: Request,
        insumo_id: int,
        inicio: date | None = None,
        dias: int = Query(7, ge=1, le=14),
        duracao_horas: float = Query(8, gt=0, le=24),
        talhao_id: int | None = None,
        fazenda_id: int | None = None,  # contrato 20: só os talhões desta fazenda (padrão: a do insumo)
    ) -> dict:
        """G2 (contrato 24): por talhão e por dia, as faixas boas e ruins para o produto, com o motivo."""
        try:
            resultado = await _servico(request, "agenda").semana(
                insumo_id, inicio, dias, duracao_horas, talhao_id, fazenda_id
            )
        except NaoEncontrado as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
        except AgendaIndisponivel as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
        return resultado.model_dump(mode="json")

    @app.post("/janelas/avaliar")
    async def avaliar_janela(pedido: PedidoJanela, request: Request) -> dict:
        """G3 (contrato 24): a janela serve? Se não, o motivo com a página e as próximas janelas boas."""
        try:
            resultado = await _servico(request, "agenda").avaliar(pedido)
        except NaoEncontrado as e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
        except AgendaIndisponivel as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(e)) from e
        return resultado.model_dump(mode="json")

    @app.post("/limites/extrair")
    async def extrair_limites(corpo: CorpoLimites, request: Request) -> dict:
        """Contrato 26: lê a bula do produto uma vez e guarda os limites conferidos em código."""
        resultado = await _servico(request, "limites").extrair(corpo.produto.strip())
        return resultado.model_dump(mode="json")

    @app.post("/relatorios/os/{os_id}", status_code=status.HTTP_201_CREATED)
    async def gerar_relatorio(os_id: int, request: Request) -> dict:
        """G7: gera o PDF da aplicação a partir da execução registrada (contrato 28)."""
        try:
            registro = await _servico(request, "relatorio").gerar(os_id)
        except RelatorioIndisponivel as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
        return {**registro, "gerado_em": registro["gerado_em"].isoformat()}

    return app


app = criar_api()
