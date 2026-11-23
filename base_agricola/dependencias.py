import os
from functools import lru_cache

import httpx

from base_agricola.agrofit import ClienteAgrofit
from base_agricola.db import criar_engine
from base_agricola.repositorio import RepositorioConhecimento


@lru_cache
def get_repo() -> RepositorioConhecimento:
    return RepositorioConhecimento(criar_engine())


@lru_cache
def get_busca() -> tuple:
    """(cliente Qdrant, embedder, coleção) — criados uma vez, no primeiro pedido."""
    from qdrant_client import QdrantClient

    from base_agricola.embeddings import FastEmbedEmbedder
    from base_agricola.qdrant import COLECAO

    return QdrantClient(url=os.environ["QDRANT_URL"]), FastEmbedEmbedder(), COLECAO


@lru_cache
def get_agrofit() -> ClienteAgrofit | None:
    """None sem AGROFIT_CLIENT_ID/AGROFIT_CLIENT_SECRET (as rotas respondem 503)."""
    id_, segredo = os.environ.get("AGROFIT_CLIENT_ID"), os.environ.get("AGROFIT_CLIENT_SECRET")
    return ClienteAgrofit(httpx.Client(timeout=15), id_, segredo) if id_ and segredo else None
