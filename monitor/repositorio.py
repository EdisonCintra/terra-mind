import json
import logging
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from monitor.historico import Conferencia, Suspensao, janela
from monitor.modelos import Estacao, Gatilho, HoraPrevisao, LeituraObservada, Previsao, Talhao

log = logging.getLogger("monitor")


class RepositorioMonitor:
    # A mesma busca do Contexto do `agents` (`erp_mcp.leitura.listar_os` + filtro de status em
    # agents/nodes/contexto.py): início previsto entre :i e :f (BETWEEN, inclusive) e status 'agendada'. Só
    # 'agendada': OS reagendada o Contexto não considera. A exigência de produto (OS de aplicação, decisão do
    # Edison) é a mais: o conjunto daqui fica dentro do de lá, então um incidente aberto aqui sempre acha OS.
    _SQL_OS_NA_JANELA = (
        "SELECT EXISTS (SELECT 1 FROM erp.ordens_servico WHERE talhao_id = :t "
        "AND status = 'agendada' AND insumo_id IS NOT NULL "
        "AND inicio_previsto BETWEEN :i AND :f)")

    def __init__(self, engine: Engine):
        self.engine = engine
        self._os_ilegivel_avisado = False

    def talhoes(self, ids: list[int] | None = None) -> list[Talhao]:
        # Talhão desativado no cadastro (F1) não é vigiado; o novo entra no ciclo seguinte.
        sql = "SELECT id, nome, lat, lon FROM erp.talhoes WHERE ativo"
        params = {}
        if ids is not None:
            sql += " AND id = ANY(:ids)"
            params["ids"] = ids
        with self.engine.connect() as conn:
            return [Talhao(**linha) for linha in conn.execute(text(sql + " ORDER BY id"), params).mappings()]

    def estacoes(self) -> list[Estacao]:
        with self.engine.connect() as conn:
            return [Estacao(**linha) for linha in conn.execute(
                text("SELECT codigo, nome, lat::float AS lat, lon::float AS lon, fonte "
                     "FROM clima.estacoes ORDER BY codigo")).mappings()]

    def gravar_observacoes(self, estacao: str, leituras: list[LeituraObservada]) -> int:
        """Grava as leituras da estação; hora já gravada é ignorada. Devolve quantas linhas entraram."""
        if not leituras:
            return 0
        linhas = [{"e": estacao, **l.model_dump(include={"ts", "chuva_mm", "temp_c", "umidade_pct",
                                                         "vento_kmh", "vento_dir_graus"})} for l in leituras]
        with self.engine.begin() as conn:
            return conn.execute(
                text("INSERT INTO clima.observacoes (estacao, ts, chuva_mm, temp_c, umidade_pct, vento_kmh, "
                     "vento_dir_graus) VALUES (:e, :ts, :chuva_mm, :temp_c, :umidade_pct, :vento_kmh, "
                     ":vento_dir_graus) ON CONFLICT DO NOTHING"), linhas).rowcount

    def observacoes_estacao(self, estacao: str, inicio: datetime, fim: datetime,
                            distancia_km: float | None = None) -> list[LeituraObservada]:
        """Leituras gravadas da estação em [inicio, fim)."""
        with self.engine.connect() as conn:
            linhas = conn.execute(
                text("SELECT ts, chuva_mm::float AS chuva_mm, temp_c::float AS temp_c, "
                     "umidade_pct::float AS umidade_pct, vento_kmh::float AS vento_kmh, "
                     "vento_dir_graus::float AS vento_dir_graus FROM clima.observacoes "
                     "WHERE estacao = :e AND ts >= :i AND ts < :f ORDER BY ts"),
                {"e": estacao, "i": inicio, "f": fim}).mappings().all()
        return [LeituraObservada(**l, fonte="estacao", estacao=estacao, distancia_km=distancia_km)
                for l in linhas]

    def suspensoes_a_conferir(self) -> list[Suspensao]:
        """Suspensões de incidentes ao vivo ainda sem conferência. O horário vem da auditoria (a OS pode
        ter sido reagendada); vale a linha do erp_mcp, a do `antes` preenchido, com a OS como estava."""
        with self.engine.connect() as conn:
            suspensas = conn.execute(text(
                "SELECT DISTINCT ON (a.incidente_id, (a.antes->>'id')::int) a.incidente_id, "
                "(a.antes->>'id')::int AS os_id, i.talhao_id, a.antes->>'inicio_previsto' AS inicio, "
                "(a.antes->>'duracao_horas')::float AS duracao, i.previsao_snapshot AS snapshot "
                "FROM agente.auditoria a JOIN agente.incidentes i ON i.id = a.incidente_id "
                "WHERE a.no = 'acao_preventiva' AND a.acao = 'suspender_os' AND a.antes IS NOT NULL "
                "AND i.origem = 'live' AND NOT EXISTS (SELECT 1 FROM clima.conferencias c "
                "WHERE c.incidente_id = a.incidente_id AND c.os_id = (a.antes->>'id')::int) "
                "ORDER BY a.incidente_id, (a.antes->>'id')::int, a.id")).mappings().all()
            avaliacoes: dict[int, list[dict]] = {}
            if suspensas:
                for linha in conn.execute(text(
                        "SELECT incidente_id, depois FROM agente.auditoria WHERE no = 'avaliador' "
                        "AND acao = 'avaliar_risco' AND incidente_id = ANY(:ids) ORDER BY id"),
                        {"ids": list({s["incidente_id"] for s in suspensas})}).mappings():
                    avaliacoes.setdefault(linha["incidente_id"], []).append(linha["depois"] or {})
        return [self._suspensao(s, avaliacoes.get(s["incidente_id"], [])) for s in suspensas]

    @staticmethod
    def _suspensao(s, avaliacoes: list[dict]) -> Suspensao:
        # a avaliação mais recente que ainda cita violações desta OS
        violacoes: list[dict] = []
        for depois in avaliacoes:
            achadas = [v for v in depois.get("violacoes", []) if v.get("os_id") == s["os_id"] and v.get("viola")]
            violacoes = achadas or violacoes
        chuva = next((v for v in violacoes if v.get("parametro") == "chuva"), None)
        inicio = datetime.fromisoformat(s["inicio"])
        suspensao = Suspensao(
            incidente_id=s["incidente_id"], os_id=s["os_id"], talhao_id=s["talhao_id"],
            inicio_previsto=inicio, duracao_horas=s["duracao"],
            parametros=list(dict.fromkeys(v["parametro"] for v in violacoes)),
            limite_chuva_mm=chuva["limite"] if chuva else None,
            janela_h=chuva["janela_h"] if chuva else None,
            limite_fonte=f"{chuva['fonte_doc']}:p.{chuva['pagina']}" if chuva else None)
        if chuva:
            fim = janela(suspensao)[1]
            suspensao.chuva_prevista_mm = sum(
                h["chuva_mm"] for h in s["snapshot"]
                if inicio <= datetime.fromisoformat(h["ts"]) < fim and h.get("chuva_mm") is not None)
        return suspensao

    def gravar_conferencia(self, c: Conferencia) -> None:
        """Uma conferência por (incidente, OS); repetir é ignorado."""
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO clima.conferencias (incidente_id, os_id, janela_inicio, janela_fim, "
                     "chuva_prevista_mm, chuva_observada_mm, limite_mm, limite_fonte, fonte_observado, "
                     "estacao, classe, motivo) VALUES (:incidente_id, :os_id, :janela_inicio, :janela_fim, "
                     ":chuva_prevista_mm, :chuva_observada_mm, :limite_mm, :limite_fonte, :fonte_observado, "
                     ":estacao, :classe, :motivo) ON CONFLICT (incidente_id, os_id) DO NOTHING"),
                c.model_dump())

    def tem_incidente_aberto(self, talhao_id: int) -> bool:
        """Qualquer incidente não encerrado bloqueia um novo, por mais antigo que seja (contrato 3)."""
        with self.engine.connect() as conn:
            return conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM agente.incidentes "
                     "WHERE talhao_id = :t AND status <> 'encerrado')"), {"t": talhao_id}
            ).scalar_one()

    def tem_os_na_janela(self, talhao_id: int, inicio: datetime, fim: datetime) -> bool:
        """Há OS de aplicação agendada do talhão com início em [inicio, fim]? Tabela de OS ilegível vale
        "sem OS" (avisa uma vez)."""
        try:
            with self.engine.connect() as conn:
                return bool(conn.execute(text(self._SQL_OS_NA_JANELA),
                                         {"t": talhao_id, "i": inicio, "f": fim}).scalar_one())
        except DBAPIError:
            if not self._os_ilegivel_avisado:
                self._os_ilegivel_avisado = True
                log.warning("ordens de serviço ilegíveis; rajada, delta_t e inversão ficam só no risco",
                            exc_info=True)
            return False

    def incidente_sem_andamento(self, talhao_id: int) -> int | None:
        """Incidente que o agents ainda não tirou de `aberto` (contrato 3)."""
        with self.engine.connect() as conn:
            return conn.execute(
                text("SELECT id FROM agente.incidentes WHERE talhao_id = :t AND status = 'aberto'"),
                {"t": talhao_id},
            ).scalar_one_or_none()

    def criar_incidente(self, talhao_id: int, gatilhos: list[Gatilho], snapshot: list[HoraPrevisao],
                        origem: Literal["live", "replay"]) -> int:
        """`origem='replay'` (contrato 27) tira o incidente do histórico e do dashboard de perda."""
        with self.engine.begin() as conn:
            return conn.execute(
                text("INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status, origem) "
                     "VALUES (:t, CAST(:g AS jsonb), CAST(:s AS jsonb), 'aberto', :o) RETURNING id"),
                {"t": talhao_id, "o": origem,
                 "g": json.dumps([g.model_dump(mode="json") for g in gatilhos]),
                 "s": json.dumps([h.model_dump(mode="json") for h in snapshot])},
            ).scalar_one()

    def gravar_previsao(self, talhao_id: int, fonte: str, previsao: Previsao) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                text("INSERT INTO clima.previsoes (talhao_id, fonte, horas, dias) "
                     "VALUES (:t, :f, CAST(:h AS jsonb), CAST(:d AS jsonb))"),
                {"t": talhao_id, "f": fonte,
                 "h": json.dumps([h.model_dump(mode="json") for h in previsao.horas]),
                 "d": json.dumps([d.model_dump(mode="json") for d in previsao.dias])},
            )

    def ultima_previsao(self, talhao_id: int) -> tuple[datetime, str, Previsao] | None:
        """Previsão mais recente do talhão: (buscada_em, fonte, previsão), ou None se nunca houve."""
        with self.engine.connect() as conn:
            linha = conn.execute(
                text("SELECT buscada_em, fonte, horas, dias FROM clima.previsoes "
                     "WHERE talhao_id = :t ORDER BY buscada_em DESC, id DESC LIMIT 1"), {"t": talhao_id}
            ).mappings().first()
        if linha is None:
            return None
        return linha["buscada_em"], linha["fonte"], Previsao(horas=linha["horas"], dias=linha["dias"])
