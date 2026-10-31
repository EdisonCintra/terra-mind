import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from base_agricola import rotas_fatores, rotas_trechos
from base_agricola.dependencias import get_busca, get_repo

__all__ = ["app", "get_busca", "get_repo"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Carrega o modelo de embeddings na subida: no primeiro pedido levaria ~10 s,
    # mais que o timeout de 5 s da ferramenta buscar_trechos.
    if os.environ.get("QDRANT_URL"):
        get_busca()
    yield


app = FastAPI(title="TerraMind · Base agrícola", lifespan=lifespan)
app.include_router(rotas_fatores.router)
app.include_router(rotas_trechos.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
