import json
from datetime import datetime, timedelta

import pytest

from monitor.historico import Suspensao, conferir, conferir_pendentes
from monitor.main import SP
from monitor.modelos import Estacao, LeituraObservada, Talhao
from monitor.observado import FonteObservadaIndisponivel

INICIO = datetime(2026, 10, 20, 6, 0, tzinfo=SP)
DEPOIS = INICIO + timedelta(days=2)  # a janela (3 h da OS + 6 h) termina às 15:00 do dia 20
ESTACAO = Estacao(codigo="A1", nome="x", lat=-17.8, lon=-50.9, fonte="inmet")


def suspensao(**kw) -> Suspensao:
    base = dict(incidente_id=1, os_id=1, talhao_id=1, inicio_previsto=INICIO, duracao_horas=3.0,
                parametros=["chuva"], limite_chuva_mm=5.0, janela_h=6.0, limite_fonte="bula.pdf:p.4",
                chuva_prevista_mm=12.0)
    return Suspensao(**{**base, **kw})


def horas(n=9, mm=1.0, fonte="estacao", estacao="A1", pular=()):
    return [LeituraObservada(ts=INICIO + timedelta(hours=i), chuva_mm=mm, fonte=fonte, estacao=estacao)
            for i in range(n) if i not in pular]


def test_choveu_acima_do_limite_correta():
    c = conferir(suspensao(), horas(mm=1.0), DEPOIS)  # 9 mm > 5 mm
    assert c.classe == "correta" and c.chuva_observada_mm == pytest.approx(9.0)
    assert c.chuva_prevista_mm == 12.0 and c.limite_mm == 5.0 and c.limite_fonte == "bula.pdf:p.4"
    assert c.fonte_observado == "estacao" and c.estacao == "A1"
    assert c.janela_inicio == INICIO and c.janela_fim == INICIO + timedelta(hours=9)


def test_choveu_abaixo_falso_alarme():
    c = conferir(suspensao(), horas(mm=0.2, fonte="reanalise", estacao=None), DEPOIS)
    assert c.classe == "falso_alarme" and c.fonte_observado == "reanalise" and c.estacao is None


def test_limite_exato_nao_e_correta():
    assert conferir(suspensao(limite_chuva_mm=9.0), horas(mm=1.0), DEPOIS).classe == "falso_alarme"


def test_hora_faltando_fica_pendente():
    assert conferir(suspensao(), horas(pular=(3,)), DEPOIS) is None
    assert conferir(suspensao(), horas(mm=None), DEPOIS) is None  # chuva None não conta como medida


def test_dez_dias_sem_dado():
    agora = INICIO + timedelta(hours=9) + timedelta(days=10)
    c = conferir(suspensao(), horas(pular=(3,)), agora)
    assert c.classe == "sem_dado" and c.motivo == "sem dado observado"
    assert c.chuva_observada_mm is None and c.fonte_observado is None
    assert c.chuva_prevista_mm == 12.0


def test_vento_vira_sem_dado_com_motivo():
    s = suspensao(parametros=["vento"], limite_chuva_mm=None, janela_h=None, limite_fonte=None,
                  chuva_prevista_mm=0.0)
    c = conferir(s, [], INICIO)
    assert c.classe == "sem_dado" and c.motivo == "só chuva é conferida"
    assert c.chuva_prevista_mm is None and c.chuva_observada_mm is None and c.limite_mm is None


def test_hora_inicial_quebrada_conta_a_hora_cheia():
    s = suspensao(inicio_previsto=INICIO + timedelta(minutes=30))  # 06:30 a 15:30 -> horas 06..15
    assert conferir(s, horas(n=9), DEPOIS) is None
    assert conferir(s, horas(n=10), DEPOIS).classe == "correta"


# ---- orquestração (sem banco) ----

class RepoFalso:
    def __init__(self, suspensoes, estacoes=(), obs=(), talhao_falha=()):
        self._s, self._e, self._o = list(suspensoes), list(estacoes), list(obs)
        self.talhao_falha = talhao_falha
        self.gravadas = []

    def suspensoes_a_conferir(self):
        return self._s

    def talhoes(self, ids=None):
        if ids and ids[0] in self.talhao_falha:
            raise RuntimeError("boom")
        return [Talhao(id=ids[0], nome="T", lat=-17.79, lon=-50.92)]

    def estacoes(self):
        return self._e

    def observacoes_estacao(self, estacao, inicio, fim, distancia_km=None):
        return self._o

    def gravar_conferencia(self, c):
        self.gravadas.append(c)


class FonteFalsa:
    def __init__(self, leituras=(), erro=False):
        self.leituras_, self.erro, self.chamadas = list(leituras), erro, 0

    def leituras(self, lat, lon, inicio, fim):
        self.chamadas += 1
        if self.erro:
            raise FonteObservadaIndisponivel("fora")
        return self.leituras_


def test_estacao_tem_prioridade_e_reanalise_nao_e_chamada():
    repo = RepoFalso([suspensao()], [ESTACAO], horas(mm=1.0))
    fonte = FonteFalsa(horas(mm=0, fonte="reanalise", estacao=None))
    assert conferir_pendentes(repo, fonte, DEPOIS) == 1
    assert repo.gravadas[0].fonte_observado == "estacao" and fonte.chamadas == 0


def test_estacao_incompleta_cai_na_reanalise():
    repo = RepoFalso([suspensao()], [ESTACAO], horas(pular=(2,)))
    fonte = FonteFalsa(horas(mm=0.1, fonte="reanalise", estacao=None))
    conferir_pendentes(repo, fonte, DEPOIS)
    assert repo.gravadas[0].fonte_observado == "reanalise" and repo.gravadas[0].classe == "falso_alarme"


def test_janela_nao_terminada_nao_chama_fonte():
    repo, fonte = RepoFalso([suspensao()]), FonteFalsa()
    assert conferir_pendentes(repo, fonte, INICIO + timedelta(hours=2)) == 0
    assert fonte.chamadas == 0 and repo.gravadas == []


def test_fonte_fora_do_ar_deixa_pendente():
    repo = RepoFalso([suspensao()])
    assert conferir_pendentes(repo, FonteFalsa(erro=True), DEPOIS) == 0 and repo.gravadas == []


def test_erro_numa_suspensao_nao_derruba_as_outras():
    repo = RepoFalso([suspensao(os_id=1, talhao_id=1), suspensao(os_id=2, talhao_id=2)], talhao_falha=(1,))
    fonte = FonteFalsa(horas(mm=1.0, fonte="reanalise", estacao=None))
    assert conferir_pendentes(repo, fonte, DEPOIS) == 1
    assert [c.os_id for c in repo.gravadas] == [2]


# ---- banco ----

@pytest.fixture
def repo_pg(engine_pg):
    from sqlalchemy import text

    from monitor.repositorio import RepositorioMonitor

    with engine_pg.begin() as conn:
        conn.execute(text("TRUNCATE clima.conferencias, clima.observacoes, clima.estacoes, agente.auditoria, "
                          "agente.incidentes, erp.ordens_servico, erp.talhoes RESTART IDENTITY CASCADE"))
        conn.execute(text("INSERT INTO erp.talhoes (fazenda_id, nome, area_ha, cultura, data_plantio, estagio_fenologico, "
                          "lat, lon) VALUES (1, 'T1', 120, 'milho', current_date, 'V6', -17.79, -50.92)"))
    return RepositorioMonitor(engine_pg)


def criar_os(engine, inicio):
    from sqlalchemy import text

    with engine.begin() as conn:
        return conn.execute(text(
            "INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, inicio_previsto, duracao_horas, status) "
            "VALUES (1, 'pulverizacao', :i, 3, 'agendada') RETURNING id"), {"i": inicio}).scalar_one()


def auditar(engine, iid, no, acao, antes, depois):
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("INSERT INTO agente.auditoria (incidente_id, no, acao, antes, depois) "
                          "VALUES (:i, :n, :a, CAST(:b AS jsonb), CAST(:d AS jsonb))"),
                     {"i": iid, "n": no, "a": acao, "b": None if antes is None else json.dumps(antes),
                      "d": None if depois is None else json.dumps(depois)})


def fluxo_suspensao(repo, engine, origem="live", reagendar_para=None):
    """Incidente com previsão de 2 mm/h, avaliação (limite 5 mm em 6 h) e as duas linhas de suspender_os."""
    from sqlalchemy import text

    from monitor.modelos import HoraPrevisao

    snap = [HoraPrevisao(ts=INICIO + timedelta(hours=i), chuva_mm=2.0, vento_kmh=5, umidade_pct=70, temp_c=25)
            for i in range(-2, 12)]
    iid = repo.criar_incidente(1, [], snap, origem)
    os_id = criar_os(engine, INICIO)
    viol = {"os_id": os_id, "viola": True, "parametro": "chuva", "limite": 5.0, "janela_h": 6.0,
            "sentido": "max", "fonte_doc": "bula.pdf", "pagina": 4, "trecho": "t", "justificativa": "j"}
    auditar(engine, iid, "avaliador", "avaliar_risco", None, {"violacoes": [{**viol, "os_id": 99, "viola": False}, viol]})
    antes = {"id": os_id, "talhao_id": 1, "inicio_previsto": INICIO.isoformat(), "duracao_horas": 3.0,
             "status": "agendada"}
    auditar(engine, iid, "acao_preventiva", "suspender_os", antes, {"id": os_id, "status": "suspensa_preventiva"})
    auditar(engine, iid, "acao_preventiva", "suspender_os", None, {"suspensas": [os_id], "impactos": []})
    if reagendar_para is not None:
        with engine.begin() as conn:
            conn.execute(text("UPDATE erp.ordens_servico SET inicio_previsto = :i, status = 'reagendada' "
                              "WHERE id = :o"), {"i": reagendar_para, "o": os_id})
    return iid, os_id


@pytest.mark.pg
def test_usa_horario_original_mesmo_reagendada(repo_pg, engine_pg):
    iid, os_id = fluxo_suspensao(repo_pg, engine_pg, reagendar_para=INICIO + timedelta(days=5))
    [s] = repo_pg.suspensoes_a_conferir()
    assert (s.incidente_id, s.os_id, s.talhao_id) == (iid, os_id, 1)
    assert s.inicio_previsto == INICIO and s.duracao_horas == 3.0 and s.parametros == ["chuva"]
    assert s.limite_chuva_mm == 5.0 and s.janela_h == 6.0 and s.limite_fonte == "bula.pdf:p.4"
    assert s.chuva_prevista_mm == pytest.approx(18.0)  # 9 h da janela x 2 mm


@pytest.mark.pg
def test_replay_ignorado(repo_pg, engine_pg):
    fluxo_suspensao(repo_pg, engine_pg, origem="replay")
    assert repo_pg.suspensoes_a_conferir() == []


@pytest.mark.pg
def test_conferencia_nao_duplica(repo_pg, engine_pg):
    from sqlalchemy import text

    fluxo_suspensao(repo_pg, engine_pg)
    fonte = FonteFalsa(horas(mm=1.0, fonte="reanalise", estacao=None))
    assert conferir_pendentes(repo_pg, fonte, DEPOIS) == 1
    assert repo_pg.suspensoes_a_conferir() == []
    assert conferir_pendentes(repo_pg, fonte, DEPOIS) == 0
    with engine_pg.connect() as conn:
        linhas = conn.execute(text("SELECT classe, fonte_observado, chuva_observada_mm, limite_fonte "
                                   "FROM clima.conferencias")).all()
    assert len(linhas) == 1 and linhas[0][0] == "correta" and linhas[0][1] == "reanalise"
    assert float(linhas[0][2]) == 9.0 and linhas[0][3] == "bula.pdf:p.4"


@pytest.mark.pg
def test_gravar_conferencia_duas_vezes_ignora_a_segunda(repo_pg, engine_pg):
    fluxo_suspensao(repo_pg, engine_pg)
    [s] = repo_pg.suspensoes_a_conferir()
    c = conferir(s, horas(), DEPOIS)
    repo_pg.gravar_conferencia(c)
    repo_pg.gravar_conferencia(c)  # ON CONFLICT DO NOTHING


@pytest.mark.pg
def test_observacoes_estacao_traz_so_a_janela(repo_pg, engine_pg):
    from sqlalchemy import text

    with engine_pg.begin() as conn:
        conn.execute(text("INSERT INTO clima.estacoes VALUES ('A1', 'x', -17.8, -50.9, 'inmet')"))
    repo_pg.gravar_observacoes("A1", [l.model_copy(update={"ts": INICIO + timedelta(hours=i)})
                                      for i, l in enumerate(horas(n=12))])
    obs = repo_pg.observacoes_estacao("A1", INICIO, INICIO + timedelta(hours=9), 3.2)
    assert len(obs) == 9 and obs[0].fonte == "estacao" and obs[0].estacao == "A1" and obs[0].distancia_km == 3.2
