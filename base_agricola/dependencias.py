import os
from functools import lru_cache

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
