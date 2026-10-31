from datetime import timedelta

import pytest

from monitor.ciclo import executar_ciclo
from monitor.modelos import HoraPrevisao, Limiares, Talhao
from monitor.tests.conftest import AGORA

LIMIARES = Limiares()


def previsao(chuva_total=80):
    return [HoraPrevisao(ts=AGORA + timedelta(hours=i), chuva_mm=chuva_total / 20 if 14 <= i < 34 else 0,
                         vento_kmh=5, umidade_pct=70, temp_c=25) for i in range(120)]


class FonteFalsa:
    def __init__(self, quebrados=()):
        self.horas, self.quebrados = previsao(), set(quebrados)

    def obter(self, lat, lon, agora):
        if (lat, lon) in self.quebrados:
            raise RuntimeError("fonte fora do ar")
        return self.horas


class RepoFalso:
    def __init__(self, talhoes):
        self._talhoes, self.abertos, self.snapshots, self.gatilhos = talhoes, set(), {}, {}
        self.sem_andamento = {}

    def talhoes(self, ids=None):
        return [t for t in self._talhoes if ids is None or t.id in ids]

    def tem_incidente_aberto(self, talhao_id):
        return talhao_id in self.abertos

    def incidente_sem_andamento(self, talhao_id):
        return self.sem_andamento.get(talhao_id)

    def criar_incidente(self, talhao_id, gatilhos, snapshot):
        novo = len(self.snapshots) + 1
        self.abertos.add(talhao_id)
        self.sem_andamento[talhao_id] = novo
        self.snapshots[novo], self.gatilhos[novo] = snapshot, gatilhos
        return novo


class NotificadorFalso:
    def __init__(self, quebrado=False):
        self.chamadas, self.quebrado = [], quebrado

    def iniciar(self, incidente_id):
        if self.quebrado:
            raise RuntimeError("agents fora do ar")
        self.chamadas.append(incidente_id)


T1, T2 = Talhao(id=1, nome="T1", lat=-17.79, lon=-50.92), Talhao(id=2, nome="T2", lat=-17.81, lon=-50.90)


def test_abre_um_incidente_e_avisa():
    repo, notif = RepoFalso([T1]), NotificadorFalso()
    r = executar_ciclo(FonteFalsa(), repo, notif, LIMIARES, AGORA)
    assert r.incidentes == [1] and notif.chamadas == [1] and r.erros == []


def test_segundo_ciclo_nao_duplica():
    repo, notif = RepoFalso([T1]), NotificadorFalso()
    executar_ciclo(FonteFalsa(), repo, notif, LIMIARES, AGORA)
    repo.sem_andamento.clear()  # o agents já mudou o status
    assert executar_ciclo(FonteFalsa(), repo, notif, LIMIARES, AGORA).incidentes == []
    assert notif.chamadas == [1] and len(repo.snapshots) == 1


def test_incidente_sem_andamento_e_reavisado_sem_abrir_outro():
    repo = RepoFalso([T1])
    executar_ciclo(FonteFalsa(), repo, NotificadorFalso(quebrado=True), LIMIARES, AGORA)
    notif = NotificadorFalso()
    r = executar_ciclo(FonteFalsa(), repo, notif, LIMIARES, AGORA)
    assert r.incidentes == [1] and r.erros == [] and notif.chamadas == [1] and len(repo.snapshots) == 1


def test_reaviso_com_agents_ainda_fora_mostra_o_erro():
    repo, notif = RepoFalso([T1]), NotificadorFalso(quebrado=True)
    executar_ciclo(FonteFalsa(), repo, notif, LIMIARES, AGORA)
    r = executar_ciclo(FonteFalsa(), repo, notif, LIMIARES, AGORA)
    assert r.incidentes == [] and "notificar incidente 1" in r.erros[0] and len(repo.snapshots) == 1


def test_snapshot_guarda_todas_as_horas():
    repo, fonte = RepoFalso([T1]), FonteFalsa()
    executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA)
    assert len(repo.snapshots[1]) == len(fonte.horas) == 120


def test_sem_chuva_nao_abre_incidente():
    fonte = FonteFalsa()
    fonte.horas = previsao(chuva_total=0)
    assert executar_ciclo(fonte, RepoFalso([T1]), NotificadorFalso(), LIMIARES, AGORA).incidentes == []


def test_fonte_falha_em_um_talhao_segue_nos_outros():
    r = executar_ciclo(FonteFalsa(quebrados=[(T1.lat, T1.lon)]), RepoFalso([T1, T2]), NotificadorFalso(),
                       LIMIARES, AGORA)
    assert r.incidentes == [1] and len(r.erros) == 1 and "talhão 1" in r.erros[0]


def test_falha_no_aviso_mantem_incidente():
    r = executar_ciclo(FonteFalsa(), RepoFalso([T1]), NotificadorFalso(quebrado=True), LIMIARES, AGORA)
    assert r.incidentes == [1] and "notificar" in r.erros[0]


def test_filtra_talhoes():
    r = executar_ciclo(FonteFalsa(), RepoFalso([T1, T2]), NotificadorFalso(), LIMIARES, AGORA, talhao_ids=[2])
    assert r.incidentes == [1]


@pytest.fixture
def repo_pg(engine_pg):
    from sqlalchemy import text

    from monitor.repositorio import RepositorioMonitor

    with engine_pg.begin() as conn:
        conn.execute(text("TRUNCATE agente.incidentes, erp.talhoes RESTART IDENTITY CASCADE"))
        conn.execute(text("INSERT INTO erp.talhoes (nome, area_ha, cultura, data_plantio, "
                          "estagio_fenologico, lat, lon) "
                          "VALUES ('T1', 120, 'milho', current_date, 'V6', -17.79, -50.92)"))
    return RepositorioMonitor(engine_pg)


def mudar_status(engine, incidente_id, status, dias_atras=0):
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("UPDATE agente.incidentes SET status = :s, "
                          "criado_em = now() - make_interval(days => :d) WHERE id = :i"),
                     {"s": status, "d": dias_atras, "i": incidente_id})


def test_pg_cria_incidente_com_jsonb(repo_pg, engine_pg):
    from monitor.avaliacao import avaliar

    horas = previsao()
    iid = repo_pg.criar_incidente(1, avaliar(horas, LIMIARES, AGORA), horas)
    assert repo_pg.talhoes()[0].lat == pytest.approx(-17.79)
    assert repo_pg.tem_incidente_aberto(1) and iid == 1


def test_pg_incidente_antigo_em_decisao_manual_bloqueia(repo_pg, engine_pg):
    iid = repo_pg.criar_incidente(1, [], [])
    mudar_status(engine_pg, iid, "aguardando_decisao_manual", dias_atras=5)
    assert repo_pg.tem_incidente_aberto(1) is True


def test_pg_incidente_encerrado_nao_bloqueia(repo_pg, engine_pg):
    iid = repo_pg.criar_incidente(1, [], [])
    mudar_status(engine_pg, iid, "encerrado")
    assert repo_pg.tem_incidente_aberto(1) is False


def test_pg_incidente_sem_andamento_so_com_status_aberto(repo_pg, engine_pg):
    iid = repo_pg.criar_incidente(1, [], [])
    assert repo_pg.incidente_sem_andamento(1) == iid
    mudar_status(engine_pg, iid, "aguardando_aprovacao")
    assert repo_pg.incidente_sem_andamento(1) is None
