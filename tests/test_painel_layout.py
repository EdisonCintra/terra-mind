"""Layout do painel (E1): menu lateral, seletor de fazenda e usuário."""

import re
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from starlette.requests import Request

from painel import dados, main
from painel.main import app, contexto_base
from painel.navegacao import MENU, TODOS, ItemMenu


@pytest.fixture
def cliente():
    return TestClient(app)


def test_raiz_vai_para_visao_geral(cliente):
    r = cliente.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/visao-geral"


def test_os_redireciona_para_visao_geral(cliente):
    r = cliente.get("/os", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/visao-geral"


def test_menu_na_ordem_do_brief():
    assert [i.rota for i in MENU] == [
        "/visao-geral", "/campo", "/mapa", "/agenda", "/historico", "/bula", "/relatorios",
        "/fazenda", "/perda", "/cadastros", "/cadastros/os", "/usuarios",
    ]  # conceito 2 do Figma: Hoje, Mapa, Semana, Acertos, Bula, Relatórios e o bloco Gestão


@pytest.mark.parametrize("rota", ["/visao-geral", "/bula", "/agenda", "/perda", "/relatorios"])
def test_menu_lateral_em_todas_as_telas(cliente, rota):
    html = cliente.get(rota).text
    assert '<nav class="menu"' in html
    for item in MENU:
        assert f'href="{item.rota}"' in html and item.rotulo in html
    atuais = re.findall(r'<a [^>]*aria-current="page"[^>]*>', html)
    assert len(atuais) == 1 and f'href="{rota}"' in atuais[0]


@pytest.fixture
def menu_com_tela_futura(monkeypatch):
    """Todas as telas do menu já existem; este item fica sem rota, para testar o estado "em construção"."""
    monkeypatch.setattr(main, "MENU", [*main.MENU, ItemMenu("/em-breve", "Em breve", TODOS)])


def test_item_sem_rota_aparece_desabilitado(cliente, menu_com_tela_futura):
    html = cliente.get("/visao-geral").text
    assert re.search(r'<a [^>]*href="/em-breve"[^>]*aria-disabled="true"', html)  # as demais já existem
    assert not re.search(r'<a [^>]*href="/bula"[^>]*aria-disabled', html)


def test_seletor_de_fazenda_some_sem_fazendas(cliente, monkeypatch):
    monkeypatch.setattr(dados, "listar_fazendas", lambda: [])  # não depende de `erp.fazendas` existir (F2)
    assert 'name="fazenda_id"' not in cliente.get("/visao-geral").text


@pytest.mark.parametrize("cookie", ["²", "abc", "-1", "1.5", ""])
def test_cookie_de_fazenda_forjado_nao_derruba_a_pagina(monkeypatch, cookie):
    # O navegador manda o byte cru; o Starlette decodifica o cabeçalho em latin-1 ("\xb2" vira "²").
    monkeypatch.setattr(dados, "listar_fazendas", lambda: [{"id": 1, "nome": "Sede"}])
    cabecalho = [(b"cookie", f"fazenda_id={cookie}".encode("latin-1"))]
    request = Request({"type": "http", "app": app, "headers": cabecalho, "state": {}})
    assert contexto_base(request)["fazenda_id"] is None


def test_seletor_de_fazenda_aparece_com_fazendas_e_marca_a_do_cookie(cliente, monkeypatch):
    monkeypatch.setattr(
        dados, "listar_fazendas", lambda: [{"id": 1, "nome": "Sede"}, {"id": 2, "nome": "Boa Vista"}]
    )
    cliente.cookies.set("fazenda_id", "2")
    html = cliente.get("/visao-geral").text
    assert 'name="fazenda_id"' in html
    assert '<option value="1">Sede</option>' in html
    assert '<option value="2" selected>Boa Vista</option>' in html


def _itens(papel):
    request = Request({"type": "http", "app": app, "headers": [], "state": {"usuario": {"nome": "X", "papel": papel}}})
    return [i["rota"] for i in contexto_base(request)["itens_menu"]]


def test_cadastros_so_para_gerente():
    assert "/cadastros" in _itens("gerente")
    assert "/cadastros" not in _itens("operador")
    assert "/perda" in _itens("operador") and "/relatorios" in _itens("operador")


def test_papeis_do_menu_so_gerente_e_operador():
    assert all(i.papeis <= {"gerente", "operador"} for i in MENU)


def test_listar_fazendas_sem_tabela_devolve_vazio_e_nao_deixa_transacao_abortada(monkeypatch):
    # Simula a tabela ausente mesmo depois que o F2 criar `erp.fazendas`.
    conectar_real = dados.conectar

    class SemFazendas:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, sql, *args, **kwargs):
            return self.conn.execute(sql.replace("erp.fazendas", "erp.fazendas_inexistente"), *args, **kwargs)

    @contextmanager
    def conectar():
        with conectar_real() as conn:
            yield SemFazendas(conn)

    monkeypatch.setattr(dados, "conectar", conectar)
    assert dados.listar_fazendas() == []
    monkeypatch.setattr(dados, "conectar", conectar_real)
    assert "Abacus HC" in dados.listar_produtos()


def test_selecionar_fazenda_grava_cookie_e_volta(cliente):
    r = cliente.post(
        "/fazenda/selecionar", data={"fazenda_id": "2", "voltar": "/bula"}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/bula"
    assert "fazenda_id=2" in r.headers["set-cookie"]
    r = cliente.post(
        "/fazenda/selecionar", data={"fazenda_id": "2", "voltar": "//outro.site"}, follow_redirects=False
    )
    assert r.headers["location"] == "/visao-geral"


@pytest.mark.parametrize("voltar", [r"/\evil.com", "//evil.com", "https://evil.com", r"/\/evil.com",
                                    r"\evil.com", "evil.com", "", r"/x\y", "http:/evil.com", "///evil.com"])
def test_selecionar_fazenda_recusa_redirecionamento_para_fora(cliente, voltar):
    r = cliente.post(
        "/fazenda/selecionar", data={"fazenda_id": "2", "voltar": voltar}, follow_redirects=False
    )
    assert r.headers["location"] == "/visao-geral"


def test_selecionar_fazenda_aceita_caminho_com_consulta(cliente):
    r = cliente.post(
        "/fazenda/selecionar", data={"fazenda_id": "2", "voltar": "/visao-geral?incidente=3"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/visao-geral?incidente=3"


def test_404_com_banco_fora_registra_o_erro(cliente, monkeypatch, caplog):
    def quebra():
        raise RuntimeError("banco fora")

    monkeypatch.setattr(dados, "listar_fazendas", quebra)
    with caplog.at_level("WARNING", logger="painel"):
        r = cliente.get("/tela-que-nao-existe")
    assert r.status_code == 404
    registro = next(r for r in caplog.records if r.name == "painel")
    assert registro.exc_info and "banco fora" in str(registro.exc_info[1])


def test_visao_geral_mantem_os_blocos(cliente):
    html = cliente.get("/visao-geral").text
    assert 'hx-get="/parcial/pendentes"' in html and 'hx-get="/parcial/alertas"' in html
    assert 'id="kpi-perda"' in html


def test_vitrine_tem_todos_os_componentes(cliente):
    html = cliente.get("/componentes").text
    for trecho in ('class="kpi', "<table", 'class="selo', 'class="faixa', 'class="vazio"', "<form"):
        assert trecho in html


def test_css_tem_versao_celular(cliente):
    assert "@media (max-width: 720px)" in cliente.get("/static/painel.css").text


def test_componentes_nao_estao_no_menu():
    assert "/componentes" not in [i.rota for i in MENU]
