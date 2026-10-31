import uuid

from qdrant_client import QdrantClient, models

from base_agricola.embeddings import Embedder
from base_agricola.modelos import Trecho

CAMPOS_FILTRO = ("produto", "culturas", "secao", "doc_id")


def _id_ponto(t: Trecho) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{t.doc_id}:{t.pagina}:{t.secao}:{t.ordem}"))


def garantir_colecao(client: QdrantClient, nome: str, dimensao: int) -> None:
    if client.collection_exists(nome):
        return
    vetores = models.VectorParams(size=dimensao, distance=models.Distance.COSINE)
    client.create_collection(nome, vectors_config=vetores)
    for campo in CAMPOS_FILTRO:
        client.create_payload_index(nome, campo, models.PayloadSchemaType.KEYWORD)


def indexar(client: QdrantClient, embedder: Embedder, trechos: list[Trecho], colecao: str = "trechos") -> int:
    """Substitui todos os pontos de cada documento presente em `trechos`; rodar de novo não duplica."""
    garantir_colecao(client, colecao, embedder.dimensao)
    for doc_id in {t.doc_id for t in trechos}:
        client.delete(colecao, models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))])))
    if not trechos:
        return 0
    vetores = embedder.embed([t.texto for t in trechos])
    client.upsert(colecao, [
        models.PointStruct(id=_id_ponto(t), vector=v, payload=t.model_dump())
        for t, v in zip(trechos, vetores, strict=True)
    ])
    return len(trechos)
