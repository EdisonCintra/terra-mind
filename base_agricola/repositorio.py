from sqlalchemy import Engine, text

from base_agricola.modelos import FatorPerda


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
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO conhecimento.documentos "
                    "(id, titulo, tipo_doc, produto, culturas, url, arquivo) "
                    "VALUES (:id, :titulo, :tipo_doc, :produto, :culturas, :url, :arquivo) "
                    "ON CONFLICT (id) DO UPDATE SET titulo = EXCLUDED.titulo, tipo_doc = EXCLUDED.tipo_doc, "
                    "produto = EXCLUDED.produto, culturas = EXCLUDED.culturas, url = EXCLUDED.url, "
                    "arquivo = EXCLUDED.arquivo"
                ),
                {"id": id, "titulo": titulo, "tipo_doc": tipo_doc, "produto": produto,
                 "culturas": culturas, "url": url, "arquivo": arquivo},
            )

    def ids_documentos(self) -> set[str]:
        with self.engine.connect() as conn:
            return set(conn.execute(text("SELECT id FROM conhecimento.documentos")).scalars())
