"""F6: o operador registra a execução da OS no celular (contrato 28) e o painel pede o relatório (G7)."""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from painel import auth, dados
from painel import main as painel_main
from tests.test_painel_login import ORIGEM, SENHA, como, entrar, sql

FUSO = dados.FUSO


@pytest.fixture(autouse=True)
def relatorio_falso(monkeypatch):
    """O PDF é do `agents` (G7): aqui só conferimos que o painel o pede."""
    pedidos = []

    def gerar(os_id):
        pedidos.append(os_id)
        return "ok", f"OS {os_id} concluída. Relatório v1 gerado: dentro dos limites da bula."

    monkeypatch.setattr(painel_main, "_gerar_relatorio", gerar)
    return pedidos


@pytest.fixture
def cliente():
    return TestClient(painel_main.app)


@pytest.fixture
def outro_operador():
    return sql("INSERT INTO erp.operadores (fazenda_id, nome) VALUES (1, 'Outro Operador') RETURNING id")[0][
        0
    ]


@pytest.fixture
def logins(monkeypatch, outro_operador):
    """Login ligado: um operador ligado ao operador 1, outro ao segundo operador, e um gerente."""
    monkeypatch.setenv("PAINEL_AUTENTICACAO", "ligada")
    auth.zerar_limites()

    def criar(nome, email, papel, operador_id=None, fazendas=(1,)):
        r = auth.salvar_usuario(
            {
                "nome": nome,
                "email": email,
                "papel": papel,
                "senha": SENHA,
                "fazendas": list(fazendas),
                "operador_id": str(operador_id or ""),
            },
            1,
            {"id": 0, "nome": "teste"},
        )
        assert r.ok, r.erros

    criar("João do Campo", "joao@exemplo.com", "operador", 1)
    criar("Zeca", "zeca@exemplo.com", "operador", outro_operador)
    criar("Gerente", "chefe@exemplo.com", "gerente")
    yield
    auth.zerar_limites()


def formulario(**extra):
    inicio = (datetime.now(FUSO) - timedelta(hours=9)).replace(second=0, microsecond=0)
    base = {
        "inicio_real": f"{inicio:%Y-%m-%dT%H:%M}",
        "fim_real": f"{inicio + timedelta(hours=8):%Y-%m-%dT%H:%M}",
        "area_aplicada_ha": "118,5",
        "dose_real_por_ha": "0,3",
        "modalidade": "terrestre",
    }
    return {**base, **extra}


def status_da_os(os_id=1):
    return sql("SELECT status FROM erp.ordens_servico WHERE id = %s", (os_id,))[0][0]


# --- lista ------------------------------------------------------------------------------------
def test_lista_as_ordens_a_executar_com_link_para_registrar(cliente):
    html = cliente.get("/campo").text
    assert 'href="/os/1/concluir"' in html and 'href="/os/2/concluir"' in html
    assert "Registrar execução" in html


def test_suspensa_aparece_com_aviso_de_nao_aplicar_e_sem_link(cliente):
    sql("UPDATE erp.ordens_servico SET status = 'suspensa_preventiva' WHERE id = 2")
    html = cliente.get("/campo").text
    assert "não aplicar" in html and 'href="/os/2/concluir"' not in html
    assert cliente.get("/os/2/concluir").status_code == 404


def test_atrasada_e_marcada(cliente):
    sql("UPDATE erp.ordens_servico SET inicio_previsto = now() - interval '3 days' WHERE id = 1")
    assert "Atrasada" in cliente.get("/campo").text


def test_operador_ve_so_as_suas_e_pode_ver_todas(logins, outro_operador):
    sql("UPDATE erp.ordens_servico SET operador_id = %s WHERE id = 2", (outro_operador,))
    joao = como("joao@exemplo.com")
    minhas = joao.get("/campo").text
    assert 'href="/os/1/concluir"' in minhas and 'href="/os/2/concluir"' not in minhas
    todas = joao.get("/campo?todas=1").text
    assert 'href="/os/1/concluir"' in todas and 'href="/os/2/concluir"' in todas


def test_sem_ordem_propria_oferece_ver_todas(logins):
    html = como("zeca@exemplo.com").get("/campo").text
    assert "Nenhuma OS sua" in html and 'href="/campo?todas=1"' in html


def test_usuario_sem_operador_ligado_ve_as_da_fazenda_sem_abas(logins):
    html = como("chefe@exemplo.com").get("/campo").text
    assert 'href="/os/1/concluir"' in html and "Todas da fazenda" not in html


def test_ordem_de_outra_fazenda_nao_aparece_nem_abre(logins):
    os_demo = sql(
        "INSERT INTO erp.ordens_servico (talhao_id, tipo_operacao, inicio_previsto, duracao_horas, status)"
        " VALUES (3, 'pulverizacao', now() + interval '1 day', 4, 'agendada') RETURNING id"
    )[0][0]  # o talhão 3 é da Fazenda Demo
    joao = como("joao@exemplo.com")
    assert f'href="/os/{os_demo}/concluir"' not in joao.get("/campo?todas=1").text
    assert joao.get(f"/os/{os_demo}/concluir").status_code == 404
    assert joao.post(f"/os/{os_demo}/concluir", data=formulario(), headers=ORIGEM).status_code == 404


def test_operador_comeca_na_tela_de_executar_e_o_gerente_na_visao_geral(logins):
    for email, destino in (("joao@exemplo.com", "/campo"), ("chefe@exemplo.com", "/visao-geral")):
        cliente_, r = entrar(email)
        assert r.headers["location"] == "/"
        assert cliente_.get("/").headers["location"] == destino


# --- formulário ---------------------------------------------------------------------------------
def test_formulario_vem_preenchido_com_a_os_e_sem_horario_no_futuro(cliente):
    sql("UPDATE erp.ordens_servico SET inicio_previsto = now() + interval '5 days' WHERE id = 1")
    html = cliente.get("/os/1/concluir").text
    assert "Concluir OS 1" in html and 'value="120.00"' in html
    assert '<option value="1" selected>' in html  # máquina e operador da OS
    fim = html.split('name="fim_real" type="datetime-local" required value="')[1].split('"')[0]
    assert datetime.fromisoformat(fim).replace(tzinfo=FUSO) <= datetime.now(FUSO)


def test_formulario_so_lista_maquinas_e_operadores_da_fazenda_da_os(cliente):
    sql("INSERT INTO erp.operadores (fazenda_id, nome) VALUES (2, 'Operador da Demo')")
    assert "Operador da Demo" not in cliente.get("/os/1/concluir").text


# --- conclusão (pronto quando) --------------------------------------------------------------------
def test_conclui_grava_os_dados_reais_audita_e_pede_o_relatorio(logins, outro_operador, relatorio_falso):
    joao = como("joao@exemplo.com")
    r = joao.post(
        "/os/1/concluir",
        data=formulario(operador_id=str(outro_operador), maquina_id="1", observacoes="Vento calmo"),
        headers=ORIGEM,
    )
    assert r.status_code == 200 and "Relatório v1 gerado" in r.text
    assert r.headers["HX-Trigger"] == "relatorios-mudou"
    assert relatorio_falso == [1]
    assert status_da_os() == "executada"
    linha = sql(
        "SELECT area_aplicada_ha, operador_id, maquina_id, observacoes, registrado_por"
        " FROM erp.execucoes_os WHERE os_id = 1"
    )[0]
    assert (float(linha[0]), linha[1], linha[2], linha[3], linha[4]) == (
        118.5,
        outro_operador,
        1,
        "Vento calmo",
        "João do Campo",
    )
    justificativa = sql("SELECT justificativa FROM agente.auditoria WHERE acao = 'concluir_os'")[0][0]
    assert "João do Campo" in justificativa
    # aparece como Executada e sai da lista de campo
    assert "Executada" in joao.get("/parcial/os").text
    assert 'href="/os/1/concluir"' not in joao.get("/campo?todas=1").text


def test_sem_escolher_maquina_e_operador_valem_os_da_os(cliente):
    cliente.post("/os/1/concluir", data=formulario())
    assert sql("SELECT maquina_id, operador_id, registrado_por FROM erp.execucoes_os")[0] == (1, 1, "painel")


@pytest.mark.parametrize("campo", ["maquina_id", "operador_id"])
def test_maquina_ou_operador_de_outra_fazenda_e_recusado_sem_gravar(cliente, campo):
    tabela = "erp.maquinas" if campo == "maquina_id" else "erp.operadores"
    alheio = sql(f"SELECT id FROM {tabela} WHERE fazenda_id = 2 LIMIT 1")[0][0]
    r = cliente.post("/os/1/concluir", data=formulario(**{campo: str(alheio)}))
    assert "outra fazenda" in r.text and "HX-Trigger" not in r.headers
    assert status_da_os() == "agendada" and not sql("SELECT 1 FROM erp.execucoes_os")


def test_escolha_que_nao_e_numero_e_recusada(cliente):
    r = cliente.post("/os/1/concluir", data=formulario(maquina_id="1; drop"))
    assert "da lista" in r.text and status_da_os() == "agendada"


def test_segunda_conclusao_da_mesma_os_e_recusada(cliente, relatorio_falso):
    cliente.post("/os/1/concluir", data=formulario())
    r = cliente.post("/os/1/concluir", data=formulario())
    assert "não pode ser concluída" in r.text and relatorio_falso == [1]


def test_relatorios_aponta_para_a_tela_de_executar(cliente):
    assert 'href="/os/1/concluir"' in cliente.get("/relatorios").text
