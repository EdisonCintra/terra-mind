from datetime import timedelta

import pytest

from monitor.ciclo import executar_ciclo
from monitor.modelos import HoraPrevisao, Limiares, Previsao, Talhao
from monitor.tests.conftest import AGORA

LIMIARES = Limiares()


def previsao(chuva_total=80):
    return [HoraPrevisao(ts=AGORA + timedelta(hours=i), chuva_mm=chuva_total / 20 if 14 <= i < 34 else 0,
                         vento_kmh=5, umidade_pct=70, temp_c=25) for i in range(120)]


class FonteFalsa:
    nome = "open-meteo"

    def __init__(self, quebrados=()):
        self.horas, self.quebrados, self.chamadas = previsao(), set(quebrados), []

    def obter(self, lat, lon, agora):
        self.chamadas.append((lat, lon))
        if (lat, lon) in self.quebrados:
            raise RuntimeError("fonte fora do ar")
        # como o ReplayFonte: a previsão começa na hora de `agora` (o /ciclo e o laço usam datetime.now())
        delta = agora.replace(minute=0, second=0, microsecond=0) - self.horas[0].ts
        return Previsao(horas=[h.model_copy(update={"ts": h.ts + delta}) for h in self.horas])


class RepoFalso:
    def __init__(self, talhoes):
        self._talhoes, self.abertos, self.snapshots, self.gatilhos = talhoes, set(), {}, {}
        self.sem_andamento, self.origens, self.previsoes_gravadas = {}, {}, {}
        self.os_por_talhao, self.consultas_os = {}, []  # talhão -> [(inicio_previsto, status)] das OS de aplicação

    def tem_os_na_janela(self, talhao_id, inicio, fim):
        """Mesma regra do RepositorioMonitor (e do Contexto do agents): agendada, início em [inicio, fim]."""
        self.consultas_os.append((talhao_id, inicio, fim))
        return any(s == "agendada" and inicio <= i <= fim for i, s in self.os_por_talhao.get(talhao_id, []))

    def talhoes(self, ids=None):
        return [t for t in self._talhoes if ids is None or t.id in ids]

    def tem_incidente_aberto(self, talhao_id):
        return talhao_id in self.abertos

    def incidente_sem_andamento(self, talhao_id):
        return self.sem_andamento.get(talhao_id)

    def gravar_previsao(self, talhao_id, fonte, previsao):
        self.previsoes_gravadas[talhao_id] = (fonte, previsao)

    def ultima_previsao(self, talhao_id):
        if talhao_id not in self.previsoes_gravadas:
            return None
        fonte, prev = self.previsoes_gravadas[talhao_id]
        return AGORA, fonte, prev

    def criar_incidente(self, talhao_id, gatilhos, snapshot, origem):
        novo = len(self.snapshots) + 1
        self.origens[novo] = origem
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


def test_ciclo_live_marca_origem_live():
    repo = RepoFalso([T1])
    executar_ciclo(FonteFalsa(), repo, NotificadorFalso(), LIMIARES, AGORA)
    assert repo.origens == {1: "live"}


def test_ciclo_replay_marca_origem_replay():
    repo = RepoFalso([T1])
    executar_ciclo(FonteFalsa(), repo, NotificadorFalso(), LIMIARES, AGORA, origem="replay")
    assert repo.origens == {1: "replay"}


T1B = Talhao(id=3, nome="T1B", lat=-17.7901, lon=-50.9201)
T1C = Talhao(id=4, nome="T1C", lat=-17.7899, lon=-50.9199)


def test_talhoes_no_mesmo_ponto_dividem_consulta():
    fonte, repo = FonteFalsa(), RepoFalso([T1B, T1C])
    executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA)
    assert len(fonte.chamadas) == 1
    assert set(repo.previsoes_gravadas) == {3, 4}


def test_falha_na_consulta_nao_repete_no_mesmo_ponto():
    fonte = FonteFalsa(quebrados=[(T1B.lat, T1B.lon), (T1C.lat, T1C.lon)])
    r = executar_ciclo(fonte, RepoFalso([T1B, T1C]), NotificadorFalso(), LIMIARES, AGORA)
    assert len(fonte.chamadas) == 1 and len(r.erros) == 2


def test_ciclo_grava_previsao_mesmo_com_incidente_aberto():
    repo = RepoFalso([T1])
    repo.abertos.add(T1.id)
    executar_ciclo(FonteFalsa(), repo, NotificadorFalso(), LIMIARES, AGORA)
    fonte, previsao_gravada = repo.previsoes_gravadas[T1.id]
    assert fonte == "open-meteo" and len(previsao_gravada.horas) == 120


def test_falha_na_fonte_nao_impede_reaviso():
    repo, notif = RepoFalso([T1]), NotificadorFalso()
    repo.abertos.add(T1.id)
    repo.sem_andamento[T1.id] = 7
    r = executar_ciclo(FonteFalsa(quebrados=[(T1.lat, T1.lon)]), repo, notif, LIMIARES, AGORA)
    assert r.incidentes == [7] and notif.chamadas == [7] and "talhão 1" in r.erros[0]


def test_falha_ao_gravar_previsao_nao_impede_incidente():
    repo = RepoFalso([T1])

    def quebra(*args):
        raise RuntimeError("banco fora")

    repo.gravar_previsao = quebra
    r = executar_ciclo(FonteFalsa(), repo, NotificadorFalso(), LIMIARES, AGORA)
    assert r.incidentes == [1] and "talhão 1" in r.erros[0]


def test_filtra_talhoes():
    r = executar_ciclo(FonteFalsa(), RepoFalso([T1, T2]), NotificadorFalso(), LIMIARES, AGORA, talhao_ids=[2])
    assert r.incidentes == [1]


def previsao_seca(**campos):
    """120 h sem chuva, vento e umidade dentro do limite; `campos` vale nas horas 10..13."""
    base = {"chuva_mm": 0, "vento_kmh": 5, "umidade_pct": 70, "temp_c": 25}
    return [HoraPrevisao(ts=AGORA + timedelta(hours=i), **{**base, **(campos if 10 <= i < 14 else {})})
            for i in range(120)]


CONDICOES_SO_COM_OS = {"rajada": {"rajada_kmh": 30}, "delta_t": {"delta_t_c": 10}, "inversao": {"inversao": True}}


@pytest.mark.parametrize("tipo", sorted(CONDICOES_SO_COM_OS))
def test_condicao_de_aplicacao_sem_os_na_janela_nao_abre_incidente(tipo):
    fonte, repo = FonteFalsa(), RepoFalso([T1])
    fonte.horas = previsao_seca(**CONDICOES_SO_COM_OS[tipo])
    repo.os_por_talhao[T1.id] = [(AGORA + timedelta(hours=30), "agendada")]  # depois do gatilho
    r = executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA)
    assert r.incidentes == [] and r.erros == [] and repo.snapshots == {}
    # a janela do Contexto do agents: [max(agora, inicio − 24 h), fim], com fim = última hora violada (13 h)
    assert repo.consultas_os == [(T1.id, AGORA, AGORA + timedelta(hours=13))]


@pytest.mark.parametrize("tipo", sorted(CONDICOES_SO_COM_OS))
@pytest.mark.parametrize("horas", [0, 2, 12, 13])  # do início do ciclo até a última hora violada, inclusive
def test_condicao_de_aplicacao_com_os_na_janela_abre_incidente(tipo, horas):
    fonte, repo = FonteFalsa(), RepoFalso([T1])
    fonte.horas = previsao_seca(**CONDICOES_SO_COM_OS[tipo])
    repo.os_por_talhao[T1.id] = [(AGORA + timedelta(hours=horas), "agendada")]
    r = executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA)
    assert r.incidentes == [1] and [g.tipo for g in repo.gatilhos[1]] == [tipo]


def test_os_que_comeca_depois_da_ultima_hora_violada_nao_conta():
    """Antes valia sobreposição até fim + 1 h; o Contexto do agents só pega início até `fim`."""
    fonte, repo = FonteFalsa(), RepoFalso([T1])
    fonte.horas = previsao_seca(rajada_kmh=30)
    repo.os_por_talhao[T1.id] = [(AGORA + timedelta(hours=13, minutes=30), "agendada")]
    assert executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA).incidentes == []


def test_os_reagendada_na_janela_nao_abre_incidente():
    """R1: o Contexto do agents só considera OS `agendada`. Antes o Monitor contava a reagendada, abria o
    incidente, o agents encerrava como "nenhuma OS" e o Monitor reabria a cada 30 min."""
    fonte, repo = FonteFalsa(), RepoFalso([T1])
    fonte.horas = previsao_seca(rajada_kmh=30)
    repo.os_por_talhao[T1.id] = [(AGORA + timedelta(hours=12), "reagendada")]
    r = executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA)
    assert r.incidentes == [] and repo.snapshots == {} and len(repo.consultas_os) == 1


def test_alcance_antes_do_gatilho_igual_ao_do_contexto_dos_agents():
    contexto = pytest.importorskip("agents.nodes.contexto")
    from monitor.ciclo import ALCANCE_ANTES_DO_GATILHO

    assert ALCANCE_ANTES_DO_GATILHO == contexto.ALCANCE_ANTES_DA_CHUVA


def test_gatilho_depois_de_24h_so_olha_24h_antes():
    fonte, repo = FonteFalsa(), RepoFalso([T1])
    fonte.horas = [h.model_copy(update={"rajada_kmh": 30}) if 40 <= i < 42 else h
                   for i, h in enumerate(previsao_seca())]
    repo.os_por_talhao[T1.id] = [(AGORA + timedelta(hours=15), "agendada")]  # 25 h antes do gatilho
    assert executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA).incidentes == []
    assert repo.consultas_os == [(T1.id, AGORA + timedelta(hours=16), AGORA + timedelta(hours=41))]


def test_chuva_abre_incidente_sem_os_e_leva_so_os_gatilhos_validos():
    """Chuva não consulta OS; a rajada sem OS na janela sai da lista do incidente."""
    fonte, repo = FonteFalsa(), RepoFalso([T1])
    fonte.horas = [h.model_copy(update={"rajada_kmh": 30}) if h.chuva_mm == 0 else h for h in previsao()]
    r = executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA)
    assert r.incidentes == [1] and [g.tipo for g in repo.gatilhos[1]] == ["chuva"]


def test_vento_e_umidade_seguem_abrindo_sem_os():
    fonte, repo = FonteFalsa(), RepoFalso([T1])
    fonte.horas = previsao_seca(vento_kmh=20, umidade_pct=30)
    r = executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA)
    assert r.incidentes == [1] and [g.tipo for g in repo.gatilhos[1]] == ["vento", "umidade"]
    assert repo.consultas_os == []


def test_snapshot_do_incidente_so_leva_horas_completas_para_o_agents():
    """O `agents` não aceita None nos campos básicos (0 inventaria violação): hora incompleta fica de fora."""
    fonte, repo = FonteFalsa(), RepoFalso([T1])
    fonte.horas = previsao()
    fonte.horas[3] = fonte.horas[3].model_copy(update={"umidade_pct": None})
    executar_ciclo(fonte, repo, NotificadorFalso(), LIMIARES, AGORA)
    assert len(repo.snapshots[1]) == 119 and all(h.umidade_pct is not None for h in repo.snapshots[1])
    assert repo.previsoes_gravadas[T1.id][1].horas[3].umidade_pct is None  # a previsão guardada mantém o None


@pytest.fixture
def repo_pg(engine_pg):
    from sqlalchemy import text

    from monitor.repositorio import RepositorioMonitor

    with engine_pg.begin() as conn:
        conn.execute(text("TRUNCATE clima.previsoes, agente.incidentes, erp.talhoes RESTART IDENTITY CASCADE"))
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
    iid = repo_pg.criar_incidente(1, avaliar(horas, LIMIARES, AGORA), horas, "live")
    assert repo_pg.talhoes()[0].lat == pytest.approx(-17.79)
    assert repo_pg.tem_incidente_aberto(1) and iid == 1


@pytest.mark.parametrize("origem", ["live", "replay"])
def test_pg_grava_origem_do_incidente(repo_pg, engine_pg, origem):
    from sqlalchemy import text

    iid = repo_pg.criar_incidente(1, [], [], origem)
    with engine_pg.connect() as conn:
        assert conn.execute(text("SELECT origem FROM agente.incidentes WHERE id = :i"), {"i": iid}).scalar_one() == origem


def test_gravar_previsao_pg(repo_pg, engine_pg):
    from sqlalchemy import text

    repo_pg.gravar_previsao(1, "open-meteo", Previsao(horas=previsao()))
    with engine_pg.connect() as conn:
        linha = conn.execute(text("SELECT fonte, jsonb_array_length(horas) AS n, dias FROM clima.previsoes "
                                  "WHERE talhao_id = 1")).mappings().one()
    assert linha["fonte"] == "open-meteo" and linha["n"] == 120 and linha["dias"] == []


def test_ultima_previsao_pg(repo_pg):
    assert repo_pg.ultima_previsao(1) is None
    repo_pg.gravar_previsao(1, "open-meteo", Previsao(horas=previsao()[:3]))
    repo_pg.gravar_previsao(1, "replay", Previsao(horas=previsao()))
    buscada_em, fonte, prev = repo_pg.ultima_previsao(1)
    assert fonte == "replay" and len(prev.horas) == 120 and buscada_em.tzinfo is not None
    assert isinstance(prev.horas[0], HoraPrevisao)


def test_pg_incidente_antigo_em_decisao_manual_bloqueia(repo_pg, engine_pg):
    iid = repo_pg.criar_incidente(1, [], [], "live")
    mudar_status(engine_pg, iid, "aguardando_decisao_manual", dias_atras=5)
    assert repo_pg.tem_incidente_aberto(1) is True


def test_pg_incidente_encerrado_nao_bloqueia(repo_pg, engine_pg):
    iid = repo_pg.criar_incidente(1, [], [], "live")
    mudar_status(engine_pg, iid, "encerrado")
    assert repo_pg.tem_incidente_aberto(1) is False


def test_pg_incidente_sem_andamento_so_com_status_aberto(repo_pg, engine_pg):
    iid = repo_pg.criar_incidente(1, [], [], "live")
    assert repo_pg.incidente_sem_andamento(1) == iid
    mudar_status(engine_pg, iid, "aguardando_aprovacao")
    assert repo_pg.incidente_sem_andamento(1) is None


def inserir_os(engine, inicio, duracao=8, status="agendada", insumo=True):
    from sqlalchemy import text

    with engine.begin() as conn:
        if insumo:
            conn.execute(text("INSERT INTO erp.insumos (id, produto, tipo, unidade, estoque, preco_unitario) "
                              "VALUES (1, 'X', 'fungicida', 'L', 100, 10) ON CONFLICT (id) DO NOTHING"))
        conn.execute(text("INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, insumo_id, "
                          "inicio_previsto, duracao_horas, status) "
                          "VALUES (1, 'pulverizacao', :ins, :i, :d, :s)"),
                     {"ins": 1 if insumo else None, "i": inicio, "d": duracao, "s": status})


def test_pg_os_na_janela_e_a_busca_do_contexto_dos_agents(repo_pg, engine_pg):
    """Início previsto em [inicio, fim] (inclusive, como o BETWEEN de erp_mcp.leitura.listar_os), só
    `agendada` (filtro do Contexto) e com produto (OS de aplicação)."""
    inicio, fim = AGORA + timedelta(hours=10), AGORA + timedelta(hours=13)
    assert repo_pg.tem_os_na_janela(1, inicio, fim) is False
    inserir_os(engine_pg, AGORA + timedelta(hours=13, minutes=1))  # começa depois de `fim`
    inserir_os(engine_pg, AGORA + timedelta(hours=6))   # 6 h..14 h cobre a janela, mas começa antes de `inicio`
    inserir_os(engine_pg, AGORA + timedelta(hours=11), status="cancelada")
    inserir_os(engine_pg, AGORA + timedelta(hours=11), status="reagendada")  # o Contexto não considera
    inserir_os(engine_pg, AGORA + timedelta(hours=11), insumo=False)  # sem produto: não é aplicação
    assert repo_pg.tem_os_na_janela(1, inicio, fim) is False
    inserir_os(engine_pg, fim)  # BETWEEN inclui a borda
    assert repo_pg.tem_os_na_janela(1, inicio, fim) is True


def test_pg_os_reagendada_nao_conta(repo_pg, engine_pg):
    inserir_os(engine_pg, AGORA + timedelta(hours=11), status="reagendada")
    assert repo_pg.tem_os_na_janela(1, AGORA + timedelta(hours=10), AGORA + timedelta(hours=13)) is False


def test_pg_sem_tabela_de_os_vale_sem_os_e_avisa_uma_vez(engine_pg, caplog):
    from monitor.repositorio import RepositorioMonitor

    repo = RepositorioMonitor(engine_pg)
    # simula a tabela ausente
    repo._SQL_OS_NA_JANELA = "SELECT EXISTS (SELECT 1 FROM erp.tabela_que_nao_existe)"
    inicio, fim = AGORA, AGORA + timedelta(hours=1)
    with caplog.at_level("WARNING", logger="monitor"):
        assert repo.tem_os_na_janela(1, inicio, fim) is False
        assert repo.tem_os_na_janela(1, inicio, fim) is False
    assert len([r for r in caplog.records if "ordens de serviço" in r.getMessage()]) == 1
