import hashlib
from pathlib import Path

import pymupdf
from pydantic import BaseModel


class PaginaTexto(BaseModel):
    pagina: int
    texto: str


def extrair_paginas(pdf: Path) -> list[PaginaTexto]:
    """Texto de cada página, numerada a partir de 1 como no PDF impresso."""
    with pymupdf.open(pdf) as doc:
        return [PaginaTexto(pagina=n, texto=page.get_text("text")) for n, page in enumerate(doc, start=1)]


def sha256_arquivo(pdf: Path) -> str:
    """Impressão digital do conteúdo: decide se o PDF mudou desde a última indexação."""
    return hashlib.sha256(Path(pdf).read_bytes()).hexdigest()
