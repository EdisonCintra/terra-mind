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
