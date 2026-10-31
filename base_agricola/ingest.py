"""CLI offline: `uv run python -m base_agricola.ingest indexar`."""

import logging
import os
from pathlib import Path

import typer
from qdrant_client import QdrantClient

from base_agricola.chunking import dividir_em_trechos
from base_agricola.embeddings import Embedder
from base_agricola.manifesto import Fonte, carregar_manifesto
from base_agricola.pdf import extrair_paginas
from base_agricola.qdrant import indexar

MANIFESTO = Path(__file__).parent / "dados" / "fontes.yaml"
FATORES = Path(__file__).parent / "dados" / "fatores_perda.yaml"
log = logging.getLogger("ingest")
app = typer.Typer(no_args_is_help=True)


def indexar_fontes(fontes: list[Fonte], repo, client: QdrantClient, embedder: Embedder,
                   colecao: str = "trechos") -> dict[str, int | str]:
    """Indexa cada fonte; uma falha vira mensagem no resultado e o laço segue para a próxima."""
    resultado: dict[str, int | str] = {}
    for f in fontes:
        try:
            trechos = dividir_em_trechos(f, extrair_paginas(f.arquivo))
            resultado[f.id] = indexar(client, embedder, trechos, colecao)
            repo.upsert_documento(id=f.id, titulo=f.titulo, tipo_doc=f.tipo_doc, produto=f.produto,
                                  culturas=f.culturas, url=f.url, arquivo=str(f.arquivo))
        except Exception as erro:  # noqa: BLE001 - um documento ruim não pode parar o ingest
            log.exception("falha ao indexar %s", f.id)
            resultado[f.id] = f"erro: {erro}"
    return resultado


@app.command("indexar")
def comando_indexar(manifesto: Path = MANIFESTO, doc: str | None = None) -> None:
    from base_agricola.db import criar_engine
    from base_agricola.embeddings import FastEmbedEmbedder
    from base_agricola.repositorio import RepositorioConhecimento

    fontes = [f for f in carregar_manifesto(manifesto) if doc is None or f.id == doc]
    resultado = indexar_fontes(fontes, RepositorioConhecimento(criar_engine()),
                               QdrantClient(url=os.environ["QDRANT_URL"]), FastEmbedEmbedder())
    for doc_id, n in resultado.items():
        typer.echo(f"{doc_id}: {n if isinstance(n, str) else f'{n} trechos'}")
    if any(isinstance(n, str) for n in resultado.values()):
        raise typer.Exit(code=1)


@app.command("carregar-fatores")
def comando_carregar_fatores(arquivo: Path = FATORES) -> None:
    from base_agricola.db import criar_engine
    from base_agricola.fatores import carregar_fatores
    from base_agricola.repositorio import RepositorioConhecimento

    repo = RepositorioConhecimento(criar_engine())
    for fator in carregar_fatores(arquivo, repo.ids_documentos()):
        repo.upsert_fator(fator)
        typer.echo(f"{fator.tipo_insumo}/{fator.condicao}: {fator.fator}")


if __name__ == "__main__":
    app()
