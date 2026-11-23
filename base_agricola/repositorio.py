from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import Engine, text

from base_agricola.modelos import FatorPerda, Fonte
from base_agricola.pdf import sha256_arquivo

# Os PDFs que vão dentro da imagem; `documentos.arquivo` é relativo a esta pasta ou a BULAS_DIR.
DOCS_PACOTE = Path(__file__).parent / "dados" / "docs"


def _resolver(arquivo: str, bases: Sequence[Path]) -> Path:
    """Primeira pasta que tem o arquivo; se nenhuma, o caminho na primeira (o erro aparece ao indexar)."""
    for base in bases:
        if (base / arquivo).is_file():
            return base / arquivo
    return bases[0] / arquivo


class RepositorioConhecimento:
    def __init__(self, engine: Engine):
        self.engine = engine

    def fator_perda(self, tipo_insumo: str, condicao: str) -> FatorPerda | None:
        with self.engine.connect() as conn:
            linha = conn.execute(
                text(
                    "SELECT tipo_insumo, condicao, fator, fonte_ou_estimativa "
                    "FROM conhecimento.fatores_perda WHERE tipo_insumo = :tipo AND condicao = :condicao"
                ),
                {"tipo": tipo_insumo, "condicao": condicao},
            ).mappings().first()
        return FatorPerda(**linha) if linha else None

    def upsert_fator(self, fator: FatorPerda) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO conhecimento.fatores_perda "
                    "(tipo_insumo, condicao, fator, fonte_ou_estimativa) "
                    "VALUES (:tipo_insumo, :condicao, :fator, :fonte_ou_estimativa) "
                    "ON CONFLICT (tipo_insumo, condicao) DO UPDATE "
                    "SET fator = EXCLUDED.fator, fonte_ou_estimativa = EXCLUDED.fonte_ou_estimativa"
                ),
                fator.model_dump(),
            )

    def upsert_documento(
        self,
        id: str,
        titulo: str,
        tipo_doc: str,
        produto: str | None,
        culturas: list[str],
        url: str | None,
        arquivo: str | None,
        registro_mapa: str | None = None,
        titular: str | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO conhecimento.documentos "
                    "(id, titulo, tipo_doc, produto, culturas, url, arquivo, registro_mapa, titular) "
                    "VALUES (:id, :titulo, :tipo_doc, :produto, :culturas, :url, :arquivo, "
                    ":registro_mapa, :titular) "
                    "ON CONFLICT (id) DO UPDATE SET titulo = EXCLUDED.titulo, tipo_doc = EXCLUDED.tipo_doc, "
                    "produto = EXCLUDED.produto, culturas = EXCLUDED.culturas, url = EXCLUDED.url, "
                    "arquivo = EXCLUDED.arquivo, registro_mapa = EXCLUDED.registro_mapa, "
                    "titular = EXCLUDED.titular"
                ),
                {"id": id, "titulo": titulo, "tipo_doc": tipo_doc, "produto": produto,
                 "culturas": culturas, "url": url, "arquivo": arquivo,
                 "registro_mapa": registro_mapa, "titular": titular},
            )

    def bulas_cadastradas(self, registros: list[str], produtos: list[str],
                          ids: Sequence[str] = ()) -> list[dict]:
        """Bulas (id, produto, registro_mapa) com algum destes registros, produtos ou ids."""
        with self.engine.connect() as conn:
            linhas = conn.execute(
                text("SELECT id, produto, registro_mapa FROM conhecimento.documentos "
                     "WHERE tipo_doc = 'bula' AND (registro_mapa = ANY(:registros) "
                     "OR produto = ANY(:produtos) OR id = ANY(:ids))"),
                {"registros": registros, "produtos": produtos, "ids": list(ids)},
            ).mappings().all()
        return [dict(linha) for linha in linhas]

    def listar_bulas(self, doc: str | None = None) -> list[dict]:
        """Bulas (id, produto) cadastradas; `doc` limita a um id."""
        with self.engine.connect() as conn:
            linhas = conn.execute(
                text("SELECT id, produto FROM conhecimento.documentos "
                     "WHERE tipo_doc = 'bula' AND (CAST(:doc AS text) IS NULL OR id = :doc) ORDER BY id"),
                {"doc": doc},
            ).mappings().all()
        return [dict(linha) for linha in linhas]

    def ids_documentos(self) -> set[str]:
        with self.engine.connect() as conn:
            return set(conn.execute(text("SELECT id FROM conhecimento.documentos")).scalars())

    def documentos_a_indexar(self, base: Path | Sequence[Path], forcar: bool = False) -> list[Fonte]:
        """Documentos nunca indexados ou cujo PDF mudou desde a última indexação.

        `base`: pasta dos PDFs (ou lista delas, por prioridade). Com `forcar`, todos.
        """
        bases = [base] if isinstance(base, Path) else list(base)
        with self.engine.connect() as conn:
            linhas = conn.execute(text(
                "SELECT id, titulo, tipo_doc, produto, culturas, url, arquivo, sha256, indexado_em "
                "FROM conhecimento.documentos WHERE arquivo IS NOT NULL ORDER BY id"
            )).mappings().all()
        fila = []
        for linha in linhas:
            caminho = _resolver(linha["arquivo"], bases)
            mudou = (linha["indexado_em"] is None or not caminho.is_file()
                     or sha256_arquivo(caminho) != linha["sha256"])
            if forcar or mudou:
                campos = {k: linha[k] for k in ("id", "titulo", "tipo_doc", "produto", "culturas", "url")}
                fila.append(Fonte(**campos, arquivo=caminho))
        return fila

    def marcar_indexado(self, id: str, sha256: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("UPDATE conhecimento.documentos SET sha256 = :sha256, indexado_em = now() "
                     "WHERE id = :id"),
                {"id": id, "sha256": sha256},
            )
