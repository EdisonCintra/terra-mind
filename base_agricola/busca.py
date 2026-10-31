from qdrant_client import QdrantClient, models

from base_agricola.embeddings import Embedder
from base_agricola.modelos import TrechoEncontrado


def buscar(client: QdrantClient, embedder: Embedder, colecao: str, consulta: str,
           produto: str | None = None, cultura: str | None = None, secao: str | None = None,
           limite: int = 3) -> list[TrechoEncontrado]:
    """Busca semântica com filtro só nos campos informados."""
    if not client.collection_exists(colecao):
        return []
    condicoes = [models.FieldCondition(key=campo, match=models.MatchValue(value=valor))
                 for campo, valor in (("produto", produto), ("culturas", cultura), ("secao", secao))
                 if valor is not None]
    [vetor] = embedder.embed([consulta])
    pontos = client.query_points(colecao, query=vetor, limit=limite,
                                 query_filter=models.Filter(must=condicoes) if condicoes else None).points
    return [TrechoEncontrado(texto=p.payload["texto"], fonte_doc=p.payload["doc_id"],
                             pagina=p.payload["pagina"], secao=p.payload["secao"],
                             produto=p.payload["produto"], score=p.score)
            for p in pontos]
