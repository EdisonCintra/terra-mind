import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from base_agricola.busca import buscar
from base_agricola.dependencias import get_busca
from base_agricola.modelos import TrechoEncontrado

router = APIRouter()
log = logging.getLogger("base_agricola")


class PedidoBusca(BaseModel):
    consulta: str = Field(min_length=1)
    produto: str | None = None
    cultura: str | None = None
    secao: str | None = None
    limite: int = Field(3, ge=1, le=10)


@router.post("/trechos/busca")
def busca_trechos(pedido: PedidoBusca, busca: Annotated[tuple, Depends(get_busca)]) -> list[TrechoEncontrado]:
    client, embedder, colecao = busca
    try:
        return buscar(client, embedder, colecao, pedido.consulta, pedido.produto, pedido.cultura,
                      pedido.secao, pedido.limite)
    except Exception as erro:  # qdrant-client não usa ConnectionError; qualquer falha dele vira 503
        log.exception("busca no Qdrant falhou")
        raise HTTPException(status_code=503, detail="qdrant_indisponivel") from erro
