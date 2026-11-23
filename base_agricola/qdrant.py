import uuid

from qdrant_client import QdrantClient, models

from base_agricola.embeddings import Embedder
from base_agricola.modelos import Trecho

COLECAO = "trechos"
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


def indexar(client: QdrantClient, embedder: Embedder, trechos: list[Trecho], colecao: str = COLECAO) -> int:
    """Substitui os pontos de cada documento presente em `trechos`; rodar de novo não duplica.

    Ordem segura: embeddings, depois upsert (ids determinísticos sobrescrevem), e só então apaga os pontos
    antigos que sobraram. Uma falha em qualquer passo nunca deixa o documento sem trechos.
    """
    garantir_colecao(client, colecao, embedder.dimensao)
    if not trechos:
        return 0
    vetores = embedder.embed([t.texto for t in trechos])
    client.upsert(colecao, [
        models.PointStruct(id=_id_ponto(t), vector=v, payload=t.model_dump())
        for t, v in zip(trechos, vetores, strict=True)
    ])
    for doc_id in {t.doc_id for t in trechos}:
        novos = [_id_ponto(t) for t in trechos if t.doc_id == doc_id]
        client.delete(colecao, models.FilterSelector(filter=models.Filter(
            must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))],
            must_not=[models.HasIdCondition(has_id=novos)])))
    return len(trechos)
