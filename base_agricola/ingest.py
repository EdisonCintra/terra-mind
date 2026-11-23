"""CLI offline: `uv run python -m base_agricola.ingest indexar`."""

import logging
import os
from pathlib import Path

import typer
from qdrant_client import QdrantClient

from base_agricola.busca import buscar
from base_agricola.chunking import dividir_em_trechos
from base_agricola.embeddings import Embedder
from base_agricola.modelos import Fonte
from base_agricola.pdf import extrair_paginas, sha256_arquivo
from base_agricola.qdrant import COLECAO, indexar
from base_agricola.repositorio import DOCS_PACOTE

FATORES = Path(__file__).parent / "dados" / "fatores_perda.yaml"
log = logging.getLogger("ingest")
app = typer.Typer(no_args_is_help=True)


def pastas_de_bulas() -> list[Path]:
    """Onde procurar os PDFs: BULAS_DIR (volume, bulas enviadas) primeiro, depois os da imagem."""
    volume = os.environ.get("BULAS_DIR")
    return ([Path(volume)] if volume else []) + [DOCS_PACOTE]


def indexar_fontes(fontes: list[Fonte], repo, client: QdrantClient, embedder: Embedder,
                   colecao: str = COLECAO) -> dict[str, int | str]:
    """Indexa cada fonte e a marca como indexada; uma falha vira mensagem e o laço segue (sem marcar)."""
    resultado: dict[str, int | str] = {}
    for f in fontes:
        try:
            sha = sha256_arquivo(f.arquivo)
            trechos = dividir_em_trechos(f, extrair_paginas(f.arquivo))
            if not trechos:  # ex.: PDF escaneado; indexar([]) não apagaria nada e o sha seria marcado
                raise ValueError("PDF sem texto extraível (escaneado?); nada foi indexado")
            resultado[f.id] = indexar(client, embedder, trechos, colecao)
            repo.marcar_indexado(f.id, sha)
        except Exception as erro:  # noqa: BLE001 - um documento ruim não pode parar o ingest
            log.exception("falha ao indexar %s", f.id)
            resultado[f.id] = f"erro: {erro}"
    return resultado


def selecionar_fontes(repo, client: QdrantClient, doc: str | None, forcar: bool,
                      colecao: str = COLECAO) -> list[Fonte]:
    """Fila de indexação; --doc ou uma coleção Qdrant ausente/vazia implicam reindexar tudo o que couber."""
    if not forcar and doc is not None:
        forcar = True
    if not forcar and (not client.collection_exists(colecao) or client.count(colecao).count == 0):
        typer.echo("Coleção do Qdrant ausente ou vazia: reindexando todos os documentos.")
        forcar = True
    return [f for f in repo.documentos_a_indexar(pastas_de_bulas(), forcar) if doc is None or f.id == doc]


@app.command("indexar")
def comando_indexar(doc: str | None = None, forcar: bool = False) -> None:
    """Indexa os documentos novos ou alterados; --forcar reindexa todos; --doc reindexa só aquele documento."""
    from base_agricola.db import criar_engine
    from base_agricola.embeddings import FastEmbedEmbedder
    from base_agricola.repositorio import RepositorioConhecimento

    repo = RepositorioConhecimento(criar_engine())
    client = QdrantClient(url=os.environ["QDRANT_URL"])
    fontes = selecionar_fontes(repo, client, doc, forcar)
    resultado = indexar_fontes(fontes, repo, client, FastEmbedEmbedder())
    for doc_id, n in resultado.items():
        typer.echo(f"{doc_id}: {n if isinstance(n, str) else f'{n} trechos'}")
    if any(isinstance(n, str) for n in resultado.values()):
        raise typer.Exit(code=1)


CONSULTA_CHUVA = "chuva após a aplicação intervalo sem chuva"


HITS_CONFERENCIA = 5
FRASE_ARMAZENAMENTO = "abrigo de chuva"  # "armazenar ao abrigo de chuva": fala de estoque, não de aplicação


def fala_de_chuva_apos_aplicacao(texto: str) -> bool:
    """O trecho precisa citar chuva/precipitação por algo além da frase de armazenamento."""
    normal = " ".join(texto.lower().split()).replace(FRASE_ARMAZENAMENTO, "")
    return "chuva" in normal or "precipita" in normal


@app.command("conferir-chuva")
def comando_conferir_chuva(doc: str | None = None) -> None:
    """Mostra o melhor trecho sobre chuva de cada bula; sai com 1 se alguma não tiver trecho."""
    from base_agricola.db import criar_engine
    from base_agricola.embeddings import FastEmbedEmbedder
    from base_agricola.repositorio import RepositorioConhecimento

    bulas = RepositorioConhecimento(criar_engine()).listar_bulas(doc)
    if not bulas:
        typer.echo(f"Nenhuma bula encontrada{f' com o id {doc!r}' if doc else ''}.")
        raise typer.Exit(code=1)
    client = QdrantClient(url=os.environ["QDRANT_URL"])
    embedder = FastEmbedEmbedder()
    sem_trecho = False
    for bula in bulas:
        # sem produto, buscar() não filtraria e acharia trecho de outra bula (falso positivo)
        achados = (buscar(client, embedder, COLECAO, CONSULTA_CHUVA, produto=bula["produto"],
                          limite=HITS_CONFERENCIA) if bula["produto"] else [])
        # o melhor hit pode ser de outro assunto (lavagem de embalagem): vale o primeiro que fala de chuva
        achado = next((a for a in achados if fala_de_chuva_apos_aplicacao(a.texto)), None)
        if achado:
            texto = " ".join(achado.texto.split())[:120]
            typer.echo(f"{bula['id']} · {achado.pagina} · {texto}")
        else:
            sem_trecho = True
            typer.echo(f"{bula['id']} · SEM TRECHO")
    if sem_trecho:
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
