"""Monitor de clima: `uvicorn monitor.main:app --port 8020`."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from monitor.ciclo import executar_ciclo
from monitor.fontes import ReplayFonte
from monitor.modelos import Limiares, ResultadoCiclo

SP = ZoneInfo("America/Sao_Paulo")
CENARIOS = Path(__file__).parent / "scenarios"
log = logging.getLogger("monitor")


class PedidoReplay(BaseModel):
    cenario: str = Field(pattern=r"^[a-z0-9_]+\.json$")
    talhao_id: int | None = None


def _repo_padrao():
    from sqlalchemy import create_engine

    from monitor.repositorio import RepositorioMonitor

    return RepositorioMonitor(create_engine(os.environ["DATABASE_URL"]))


def _notificador_padrao():
    from monitor.notificador import NotificadorHttp

    return NotificadorHttp(os.environ.get("AGENTS_URL", "http://agents:8000"))


def _fonte_live_padrao():
    import httpx

    from monitor.fontes import OpenMeteoFonte

    return OpenMeteoFonte(httpx.Client())


def criar_app(repo=None, notificador=None, fonte_live=None, modo: str | None = None,
              cenarios_dir: Path = CENARIOS, intervalo_min: float | None = None,
              limiares: Limiares | None = None) -> FastAPI:
    """Monta o app; dependências reais são criadas só no primeiro uso (o import não exige banco)."""
    modo = modo or os.environ.get("MONITOR_MODE", "replay")
    intervalo_min = intervalo_min or float(os.environ.get("MONITOR_INTERVAL_MIN", "30"))
    deps = {"repo": repo, "notificador": notificador, "fonte_live": fonte_live}
    fabricas = {"repo": _repo_padrao, "notificador": _notificador_padrao, "fonte_live": _fonte_live_padrao}

    def dep(nome):
        if deps[nome] is None:
            deps[nome] = fabricas[nome]()
        return deps[nome]

    def rodar(fonte, talhao_ids=None) -> ResultadoCiclo:
        resultado = executar_ciclo(fonte, dep("repo"), dep("notificador"), limiares or Limiares.do_ambiente(),
                                   datetime.now(SP), talhao_ids)
        app.state.ultimo = resultado
        return resultado

    async def laco():
        while True:
            try:
                await asyncio.to_thread(rodar, dep("fonte_live"))
            except Exception:  # noqa: BLE001 - o loop não pode morrer
                log.exception("ciclo do monitor falhou")
            await asyncio.sleep(intervalo_min * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tarefa = asyncio.create_task(laco()) if modo == "live" else None
        yield
        if tarefa:
            tarefa.cancel()

    app = FastAPI(title="TerraMind · Monitor", lifespan=lifespan)
    app.state.ultimo = None

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status")
    def status() -> dict:
        ultimo: ResultadoCiclo | None = app.state.ultimo
        return {"modo": modo,
                "ultimo_ciclo_em": ultimo.executado_em.isoformat() if ultimo else None,
                "ultimos_incidentes": ultimo.incidentes if ultimo else [],
                "ultimos_erros": ultimo.erros if ultimo else []}

    @app.post("/replay")
    def replay(pedido: PedidoReplay) -> dict:
        caminho = cenarios_dir / pedido.cenario
        if not caminho.is_file():
            raise HTTPException(status_code=404, detail="cenario_inexistente")
        ids = [pedido.talhao_id] if pedido.talhao_id is not None else None
        r = rodar(ReplayFonte(caminho), ids)
        return {"incidentes": r.incidentes, "erros": r.erros}

    @app.post("/ciclo")
    def ciclo() -> dict:
        r = rodar(dep("fonte_live"))
        return {"incidentes": r.incidentes, "erros": r.erros}

    return app


app = criar_app()
