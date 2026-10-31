import json

from sqlalchemy import Engine, text

from monitor.modelos import Gatilho, HoraPrevisao, Talhao


class RepositorioMonitor:
    def __init__(self, engine: Engine):
        self.engine = engine

    def talhoes(self, ids: list[int] | None = None) -> list[Talhao]:
        sql = "SELECT id, nome, lat, lon FROM erp.talhoes"
        params = {}
        if ids is not None:
            sql += " WHERE id = ANY(:ids)"
            params["ids"] = ids
        with self.engine.connect() as conn:
            return [Talhao(**linha) for linha in conn.execute(text(sql + " ORDER BY id"), params).mappings()]

    def tem_incidente_aberto(self, talhao_id: int) -> bool:
        """Qualquer incidente não encerrado bloqueia um novo, por mais antigo que seja (contrato 3)."""
        with self.engine.connect() as conn:
            return conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM agente.incidentes "
                     "WHERE talhao_id = :t AND status <> 'encerrado')"), {"t": talhao_id}
            ).scalar_one()

    def incidente_sem_andamento(self, talhao_id: int) -> int | None:
        """Incidente que o agents ainda não tirou de `aberto` (contrato 3)."""
        with self.engine.connect() as conn:
            return conn.execute(
                text("SELECT id FROM agente.incidentes WHERE talhao_id = :t AND status = 'aberto'"),
                {"t": talhao_id},
            ).scalar_one_or_none()

    def criar_incidente(self, talhao_id: int, gatilhos: list[Gatilho], snapshot: list[HoraPrevisao]) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                text("INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status) "
                     "VALUES (:t, CAST(:g AS jsonb), CAST(:s AS jsonb), 'aberto') RETURNING id"),
                {"t": talhao_id,
                 "g": json.dumps([g.model_dump(mode="json") for g in gatilhos]),
                 "s": json.dumps([h.model_dump(mode="json") for h in snapshot])},
            ).scalar_one()
