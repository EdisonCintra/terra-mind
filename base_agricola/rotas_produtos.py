"""Cadastro de produto: busca no Agrofit e indexação da bula enviada por upload (E5)."""

import logging
import os
import tempfile
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from base_agricola.agrofit import ClienteAgrofit, ProdutoAgrofit, slug
from base_agricola.dependencias import get_agrofit, get_busca, get_repo
from base_agricola.ingest import indexar_fontes, pastas_de_bulas
from base_agricola.modelos import Fonte
from base_agricola.pdf import extrair_paginas, sha256_arquivo
from base_agricola.repositorio import RepositorioConhecimento

router = APIRouter()
log = logging.getLogger("base_agricola")
LIMITE_PDF = 20 * 1024 * 1024


class ProdutoBusca(ProdutoAgrofit):
    bula_id: str | None = None


class BulaIndexada(BaseModel):
    bula_id: str
    produto: str
    trechos: int


def _agrofit(cliente: ClienteAgrofit | None) -> ClienteAgrofit:
    if cliente is None:
        raise HTTPException(status_code=503, detail="agrofit_nao_configurado")
    return cliente


def _bula_do_produto(p: ProdutoAgrofit, cadastradas: list[dict]) -> str | None:
    """Mesmo registro, ou bula antiga (sem registro gravado) do mesmo produto."""
    for b in cadastradas:
        mesmo_registro = b["registro_mapa"] == p.registro_mapa
        if mesmo_registro or (b["registro_mapa"] is None and b["produto"] == p.produto):
            return b["id"]
    return None


@router.get("/produtos/busca")
def busca_produtos(q: Annotated[str, Query(min_length=3)],
                   cliente: Annotated[ClienteAgrofit | None, Depends(get_agrofit)],
                   repo: Annotated[RepositorioConhecimento, Depends(get_repo)]) -> list[ProdutoBusca]:
    try:
        produtos = _agrofit(cliente).buscar(q)
    except httpx.HTTPError as erro:
        log.exception("Agrofit indisponível")
        raise HTTPException(status_code=502, detail="agrofit_indisponivel") from erro
    cadastradas = repo.bulas_cadastradas([p.registro_mapa for p in produtos], [p.produto for p in produtos])
    return [ProdutoBusca(**p.model_dump(), bula_id=_bula_do_produto(p, cadastradas)) for p in produtos]


def _pdf_para_tmp(arquivo: UploadFile, pasta: Path) -> Path:
    conteudo = arquivo.file.read(LIMITE_PDF + 1)
    if len(conteudo) > LIMITE_PDF:
        raise HTTPException(status_code=413, detail="arquivo_grande_demais")
    pasta.mkdir(parents=True, exist_ok=True)
    # Mesma pasta do destino: o os.replace final é atômico.
    fd, caminho = tempfile.mkstemp(prefix=".envio-", suffix=".pdf", dir=pasta)
    with os.fdopen(fd, "wb") as f:
        f.write(conteudo)
    return Path(caminho)


@router.post("/produtos/{registro_mapa}/bula", status_code=201, response_model=BulaIndexada)
def enviar_bula(registro_mapa: str, arquivo: UploadFile,
                cliente: Annotated[ClienteAgrofit | None, Depends(get_agrofit)],
                busca: Annotated[tuple, Depends(get_busca)],
                repo: Annotated[RepositorioConhecimento, Depends(get_repo)]) -> object:
    try:
        produto = _agrofit(cliente).obter(registro_mapa)
    except httpx.HTTPError as erro:
        log.exception("Agrofit indisponível")
        raise HTTPException(status_code=502, detail="agrofit_indisponivel") from erro
    if produto is None:
        raise HTTPException(status_code=404, detail="registro_inexistente")

    nome_base = slug(produto.produto) or slug(registro_mapa)  # marca só de símbolos: usa o registro
    bula_id = f"bula-{nome_base}"
    outro = next((b for b in repo.bulas_cadastradas([], [produto.produto], [bula_id])
                  if b["registro_mapa"] not in (None, registro_mapa)), None)
    if outro:
        return JSONResponse(status_code=409,
                            content={"detail": "produto_ja_cadastrado", "bula_id": outro["id"]})

    pasta = pastas_de_bulas()[0]
    tmp = _pdf_para_tmp(arquivo, pasta)
    try:
        try:
            paginas = extrair_paginas(tmp)
        except Exception:  # noqa: BLE001 - qualquer falha do PyMuPDF significa "não é um PDF legível"
            paginas = None
        # Fora do except: guardar o traceback manteria o arquivo aberto, e no Windows o unlink falharia.
        if paginas is None:
            raise HTTPException(status_code=422, detail="pdf_invalido")
        if not any(p.texto.strip() for p in paginas):
            raise HTTPException(status_code=422, detail="pdf_sem_texto")

        # Indexa a partir do temporário: se falhar, o PDF e o registro antigos continuam intactos.
        titulo = f"Bula {produto.produto} ({produto.titular}, reg. MAPA {registro_mapa})"
        fonte = Fonte(id=bula_id, titulo=titulo, tipo_doc="bula", produto=produto.produto, arquivo=tmp,
                      url=produto.url_bula, culturas=[c.lower() for c in produto.culturas])
        client, embedder, colecao = busca
        resultado = indexar_fontes([fonte], repo, client, embedder, colecao)[bula_id]
        if isinstance(resultado, str):
            raise HTTPException(status_code=503, detail="indexacao_falhou")

        nome = f"{nome_base}.pdf"
        sha = sha256_arquivo(tmp)
        os.replace(tmp, pasta / nome)
        repo.upsert_documento(bula_id, titulo, "bula", produto.produto, fonte.culturas, produto.url_bula,
                              nome, registro_mapa, produto.titular)
        repo.marcar_indexado(bula_id, sha)
        return BulaIndexada(bula_id=bula_id, produto=produto.produto, trechos=resultado)
    finally:
        tmp.unlink(missing_ok=True)
