"""Monitor de clima: `uvicorn monitor.main:app --port 8020`."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field

from monitor.ciclo import executar_ciclo
from monitor.fontes import ReplayFonte, _a_2m
from monitor.historico import conferir_pendentes
from monitor.modelos import Limiares, ResultadoCiclo
from monitor.observado import FonteObservadaIndisponivel, coletar_observacoes, ultima_com_dado
from monitor.risco import Risco, classificar

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

    return OpenMeteoFonte(httpx.Client(), os.environ.get("OPEN_METEO_API_KEY") or None,
                          Limiares.do_ambiente().inversao_vento_max_kmh)


def _fonte_obs_padrao():
    import httpx

    from monitor.observado import ReanaliseOpenMeteo

    return ReanaliseOpenMeteo(httpx.Client())


CACHE_OBSERVADO = timedelta(hours=1)  # a reanálise muda pouco; poupa a cota do Open-Meteo
JANELA_OBSERVADO = timedelta(days=7)
JANELA_RISCO_H = 48


def criar_app(repo=None, notificador=None, fonte_live=None, ao_vivo: bool = True,
              cenarios_dir: Path = CENARIOS, intervalo_min: float | None = None,
              limiares: Limiares | None = None, relogio=None, fonte_obs=None, fonte_estacoes=None) -> FastAPI:
    """Monta o app; dependências reais são criadas só no primeiro uso (o import não exige banco)."""
    relogio = relogio or (lambda: datetime.now(SP))
    intervalo_min = intervalo_min or float(os.environ.get("MONITOR_INTERVAL_MIN", "30"))
    deps = {"repo": repo, "notificador": notificador, "fonte_live": fonte_live,
            "fonte_obs": fonte_obs}
    fabricas = {"repo": _repo_padrao, "notificador": _notificador_padrao, "fonte_live": _fonte_live_padrao,
                "fonte_obs": _fonte_obs_padrao}
    cache_obs: dict[int, tuple[datetime, dict | None]] = {}
    ultima_coleta: list[datetime] = []
    ultima_conferencia: list[datetime] = []

    def dep(nome):
        if deps[nome] is None:
            deps[nome] = fabricas[nome]()
        return deps[nome]

    def rodar(fonte, talhao_ids=None, origem="live") -> ResultadoCiclo:
        resultado = executar_ciclo(fonte, dep("repo"), dep("notificador"), limiares or Limiares.do_ambiente(),
                                   relogio(), talhao_ids, origem)
        app.state.ultimo = resultado
        return resultado

    def coletar() -> None:
        """Uma vez por hora grava as últimas 24 h das estações registradas (sem estação, não faz nada)."""
        agora = relogio()
        if ultima_coleta and agora - ultima_coleta[0] < timedelta(hours=1):
            return
        ultima_coleta[:] = [agora]
        coletar_observacoes(dep("repo"), fonte_estacoes, agora)

    def conferir() -> None:
        """No máximo uma vez por hora confere as suspensões ao vivo contra a chuva que caiu."""
        agora = relogio()
        if ultima_conferencia and agora - ultima_conferencia[0] < timedelta(hours=1):
            return
        ultima_conferencia[:] = [agora]
        conferir_pendentes(dep("repo"), dep("fonte_obs"), agora)

    async def laco():
        while True:
            try:
                await asyncio.to_thread(rodar, dep("fonte_live"))
            except Exception:  # noqa: BLE001 - o loop não pode morrer
                log.exception("ciclo do monitor falhou")
            try:
                await asyncio.to_thread(coletar)
            except Exception:  # noqa: BLE001
                log.exception("coleta de observações falhou")
            try:
                await asyncio.to_thread(conferir)
            except Exception:  # noqa: BLE001
                log.exception("conferência das suspensões falhou")
            await asyncio.sleep(intervalo_min * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tarefa = asyncio.create_task(laco()) if ao_vivo else None
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
        return {"ao_vivo": ao_vivo,
                "ultimo_ciclo_em": ultimo.executado_em.isoformat() if ultimo else None,
                "ultimos_incidentes": ultimo.incidentes if ultimo else [],
                "ultimos_erros": ultimo.erros if ultimo else []}

    @app.post("/replay")
    def replay(pedido: PedidoReplay) -> dict:
        caminho = cenarios_dir / pedido.cenario
        if not caminho.is_file():
            raise HTTPException(status_code=404, detail="cenario_inexistente")
        ids = [pedido.talhao_id] if pedido.talhao_id is not None else None
        r = rodar(ReplayFonte(caminho, (limiares or Limiares.do_ambiente()).inversao_vento_max_kmh), ids, "replay")
        return {"incidentes": r.incidentes, "erros": r.erros}

    @app.post("/ciclo")
    def ciclo() -> dict:
        r = rodar(dep("fonte_live"))
        return {"incidentes": r.incidentes, "erros": r.erros}

    @app.get("/previsao/{talhao_id}")
    def previsao(talhao_id: int, response: Response) -> list[dict]:
        """Horas da última previsão guardada, de agora (truncado na hora) até +120 h (contrato 19)."""
        ultima = dep("repo").ultima_previsao(talhao_id)
        if ultima is None:
            raise HTTPException(status_code=404, detail="sem_previsao")
        buscada_em, fonte, prev = ultima
        inicio = relogio().replace(minute=0, second=0, microsecond=0)
        fim = inicio + timedelta(hours=120)
        response.headers["X-Previsao-Buscada-Em"] = buscada_em.isoformat()
        response.headers["X-Previsao-Fonte"] = fonte
        return [h.model_dump(mode="json") for h in prev.horas if inicio <= h.ts < fim]

    @app.get("/previsao/{talhao_id}/diaria")
    def previsao_diaria(talhao_id: int, dias: int = Query(16, ge=1, le=16)) -> list[dict]:
        """Previsão diária (até 16 dias) da última previsão guardada; [] se a fonte não trouxe diária (replay)."""
        ultima = dep("repo").ultima_previsao(talhao_id)
        if ultima is None:
            raise HTTPException(status_code=404, detail="sem_previsao")
        return [d.model_dump(mode="json") for d in ultima[2].dias[:dias]]

    @app.get("/observado/{talhao_id}")
    def observado(talhao_id: int) -> dict:
        """Última leitura observada do talhão: reanálise sob demanda (cache de 1 h por talhão)."""
        agora = relogio()
        em_cache = cache_obs.get(talhao_id)
        if em_cache is None or agora - em_cache[0] >= CACHE_OBSERVADO:
            talhoes = dep("repo").talhoes([talhao_id])
            if not talhoes:
                raise HTTPException(status_code=404, detail="sem_observado")
            try:
                leituras = dep("fonte_obs").leituras(talhoes[0].lat, talhoes[0].lon, agora - JANELA_OBSERVADO, agora)
            except FonteObservadaIndisponivel as erro:
                log.warning("observado indisponível: %s", erro)
                raise HTTPException(status_code=503, detail="observado_indisponivel") from None
            ultima = ultima_com_dado(leituras)
            em_cache = cache_obs[talhao_id] = (agora, ultima.model_dump(mode="json") if ultima else None)
        if em_cache[1] is None:
            raise HTTPException(status_code=404, detail="sem_observado")
        # A reanálise (e a estação INMET) medem a 10 m; a tela compara com a previsão a 2 m, a altura da barra.
        # O valor bruto segue em `clima.observacoes`.
        return {**em_cache[1], "vento_kmh": _a_2m(em_cache[1].get("vento_kmh")), "altura_vento_m": 2}

    @app.get("/risco")
    def risco(fazenda_id: int | None = None) -> list[Risco]:
        """Risco das próximas 48 h por talhão, pela última previsão guardada (contrato 22)."""
        # TODO(contrato 20, F2): filtrar os talhões por fazenda_id; por ora o parâmetro é aceito e ignorado.
        agora = relogio()
        lim = limiares or Limiares.do_ambiente()
        resultado = []
        for t in dep("repo").talhoes():
            ultima = dep("repo").ultima_previsao(t.id)
            if ultima is None:
                continue
            # Previsão velha (nenhuma hora na janela) é "sem dado", não "sem risco": o talhão fica fora.
            if not any(agora <= h.ts < agora + timedelta(hours=JANELA_RISCO_H) for h in ultima[2].horas):
                continue
            resultado.append(classificar(t.id, ultima[2].horas, lim, agora, JANELA_RISCO_H))
        return resultado

    return app


app = criar_app()
