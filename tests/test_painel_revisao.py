"""Revisão visual (Task 22): seletor único de talhão, cores por estado, menu do celular, 404 e telas antigas."""

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from painel import dados, main
from painel.navegacao import TODOS, ItemMenu, barra_mobile
from tests.conftest import DATABASE_URL

RAIZ = Path(__file__).resolve().parents[1]
TELAS = ["/visao-geral", "/mapa", "/historico", "/bula", "/componentes"]


@pytest.fixture
def cliente():
    return TestClient(main.app)


def novo_incidente(talhao_id=1, status="aberto"):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        return conn.execute(
            "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)"
            " VALUES (%s, '[]', '[]', %s) RETURNING id",
            (talhao_id, status),
        ).fetchone()[0]


def valor_kpi(html, id_):
    return re.search(rf'<strong id="{id_}"([^>]*)>', html).group(1)


# --- A1: o hx-include precisa achar o alvo já na primeira carga ---------------------------------
@pytest.mark.parametrize("rota", TELAS)
def test_todo_hx_include_aponta_para_elemento_da_pagina(cliente, rota):
    html = cliente.get(rota).text
    for alvo in re.findall(r'hx-include="([^"]+)"', html):
        if alvo == "this":
            continue
        assert alvo.startswith("#"), alvo
        assert f'id="{alvo[1:]}"' in html, f"{rota}: hx-include={alvo} sem elemento na página"


# --- A2: um seletor de talhão só, levado pelo incidente --------------------------------------------
# DOM mínimo da Visão geral: #previsao (data-incidente do servidor), o seletor de talhão e o link ativo da
# linha do tempo. `trocar(...)` simula o swap do HTMX; `escolher(...)` simula o usuário mudando o select.
HARNESS_SELETOR = r"""
const erros = [];
console.error = (...a) => erros.push(a.map(String).join(" "));
const [, , arquivo, incidenteInicial, talhaoInicial] = process.argv;
const ouvintes = { body: {}, doc: {} };
const ouvir = (alvo) => (tipo, f) => { (ouvintes[alvo][tipo] = ouvintes[alvo][tipo] || []).push(f); };
const opcoes = ["1", "2"].map((v) => ({ value: v, textContent: "Talhão " + v }));
const trocas = [];
const seletor = {
  id: "talhao-previsao", value: talhaoInicial, options: opcoes,
  get selectedOptions() { return opcoes.filter((o) => o.value === this.value); },
  dispatchEvent(ev) { trocas.push(this.value); (ouvintes.doc[ev.type] || []).forEach((f) => f({ target: this })); },
};
const elementos = {
  previsao: { id: "previsao", dataset: { incidente: incidenteInicial, talhaoPadrao: "1" } },
  "talhao-previsao": seletor,
  eventos: { id: "eventos", querySelectorAll: () => [], setAttribute() {} },
  seletor: { id: "seletor", setAttribute() {} },
};
let ativo = null;
global.document = {
  getElementById: (id) => elementos[id] || null,
  querySelector: (sel) => (sel.includes("a.ativa") ? ativo : null),
  querySelectorAll: (sel) => (sel.startsWith("#seletor a") && ativo ? [ativo] : []),
  addEventListener: ouvir("doc"), body: { addEventListener: ouvir("body") },
};
global.location = { search: "" };
global.setInterval = () => 0;
global.Event = class { constructor(type) { this.type = type; } };
global.fetch = async () => ({ ok: false });
require(arquivo);
const trocar = () => (ouvintes.body["htmx:afterSwap"] || []).forEach((f) => f({ target: { id: "ordens" } }));
const passos = JSON.parse(process.argv[5]);
const valores = [];
for (const p of passos) {
  if (p.incidente) ativo = { dataset: { id: String(p.incidente), talhao: String(p.talhao), status: "aberto" } };
  if (p.escolher) { seletor.value = String(p.escolher); seletor.dispatchEvent(new Event("change")); }
  trocar();
  valores.push(seletor.value);
}
process.stdout.write(JSON.stringify({ erros, valores, trocas }));
"""


def rodar_seletor(tmp_path, incidente_inicial, talhao_inicial, passos):
    roteiro = tmp_path / "harness_seletor.js"
    roteiro.write_text(HARNESS_SELETOR, encoding="utf-8")
    saida = subprocess.run(
        ["node", str(roteiro), str(RAIZ / "painel" / "static" / "painel.js"), incidente_inicial, talhao_inicial,
         json.dumps(passos)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    resultado = json.loads(saida.stdout)
    assert resultado["erros"] == []
    return resultado


@pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")
def test_incidente_que_o_servidor_ja_escolheu_nao_mexe_no_seletor(tmp_path):
    r = rodar_seletor(tmp_path, "5", "2", [{"incidente": 5, "talhao": 2}, {}])
    assert r["valores"] == ["2", "2"] and r["trocas"] == []


# --- A3 / H2: cor só para estado ---------------------------------------------------------------
@pytest.mark.parametrize(
    "pct,tom", [(None, "neutro"), (100.0, "bom"), (80.0, "bom"), (66.7, "atencao"), (20.0, "risco")]
)
def test_tom_do_acerto_do_historico(pct, tom):
    assert main._tom_acerto(pct) == tom


def test_historico_sem_conferencia_tem_kpi_neutro(cliente):
    html = cliente.get("/historico").text
    acerto = re.search(r'<dt>acerto</dt><dd([^>]*)>', html).group(1)
    assert acerto == ' class=""' and "Nenhuma suspensão conferida ainda." in html
    assert 'class="pagina-interna acertos"' in html and 'class="painel"' in html


# --- A5: incidente sem passos -------------------------------------------------------------------
def test_incidente_sem_passos_nao_diz_que_nao_ha_incidente(cliente):
    inc = novo_incidente()
    html = cliente.get(f"/parcial/timeline?incidente={inc}").text
    assert "Este incidente ainda não tem passos" in html and "Nenhum incidente" not in html


# --- A6: painel.js não quebra nas telas sem linha do tempo -------------------------------------------
HARNESS = r"""
const erros = [];
console.error = (...a) => erros.push(a.map(String).join(" "));
global.document = {
  getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, body: { addEventListener() {} },
};
global.location = { search: "" };
global.setInterval = () => 0;
require(process.argv[2]);
process.stdout.write(JSON.stringify(erros));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node não instalado")
def test_painel_js_sem_linha_do_tempo_nao_da_erro(tmp_path):
    roteiro = tmp_path / "harness.js"
    roteiro.write_text(HARNESS, encoding="utf-8")
    saida = subprocess.run(
        ["node", str(roteiro), str(RAIZ / "painel" / "static" / "painel.js")],
        capture_output=True, text=True, timeout=30, check=True,
    )
    assert json.loads(saida.stdout) == []


# --- A7: telas antigas voltam para a Visão geral ---------------------------------------------------
@pytest.mark.parametrize(
    "rota,destino",
    [("/aprovacao", "/visao-geral"), ("/simular", "/visao-geral"), ("/timeline", "/visao-geral"),
     ("/timeline?incidente=3", "/visao-geral?incidente=3")],
)
def test_telas_antigas_redirecionam(cliente, rota, destino):
    r = cliente.get(rota, follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == destino


def test_seletor_de_incidentes_aponta_para_a_tela_do_incidente(cliente):
    inc = novo_incidente(talhao_id=2)
    html = cliente.get("/parcial/seletor").text
    assert f'href="/incidente/{inc}"' in html and 'data-talhao="2"' in html
    r = cliente.get(f"/visao-geral?incidente={inc}", follow_redirects=False)  # links antigos do Telegram
    assert r.status_code == 307 and r.headers["location"] == f"/incidente/{inc}"
    tela = cliente.get(f"/incidente/{inc}").text
    assert f'hx-get="/parcial/timeline?incidente={inc}"' in tela
    assert not list((RAIZ / "painel" / "templates").glob("timeline.html"))


# --- N1: barra do celular ------------------------------------------------------------------------
def _item(rota, existe=True):
    return {"rota": rota, "rotulo": rota.strip("/"), "existe": existe}


def test_barra_mobile_mostra_so_itens_com_tela():
    itens, mais = barra_mobile([_item("/a"), _item("/b", False), _item("/c"), _item("/d")])
    assert [i["rota"] for i in itens if i["na_barra"]] == ["/a", "/c", "/d"] and mais == []


def test_barra_mobile_com_mais_de_4_ativos_usa_mais():
    rotas = ["/a", "/b", "/c", "/d", "/e", "/f"]
    itens, mais = barra_mobile([_item(r) for r in rotas] + [_item("/x", False)])
    assert [i["rota"] for i in itens if i["na_barra"]] == rotas[:4]
    assert [i["rota"] for i in mais] == rotas  # o "Mais" abre a lista inteira de telas


@pytest.fixture
def menu_com_tela_futura(monkeypatch):
    """Todas as telas do menu já existem; este item fica sem rota, para testar o estado "em construção"."""
    monkeypatch.setattr(main, "MENU", [*main.MENU, ItemMenu("/em-breve", "Em breve", TODOS)])


def test_menu_marca_itens_fora_da_barra(cliente, menu_com_tela_futura):
    html = cliente.get("/visao-geral").text
    assert re.search(r'<li class="fora-da-barra"><a href="/em-breve"', html)  # sem tela: nunca na barra
    assert re.search(r'<li><a href="/agenda"', html) and re.search(r'<li><a href="/perda"', html)
    # com agenda, perda e relatórios (G1, G2, G7) são 7 telas: as 4 primeiras na barra, o resto no "Mais"
    assert re.search(r'<li class="fora-da-barra"><a href="/bula"', html)
    assert 'class="mais"' in html and '<a href="/relatorios"' in html.split('class="mais"')[1]


def test_menu_com_mais_tem_um_so_aria_current(cliente, monkeypatch):
    itens = [_item(r) for r in ("/visao-geral", "/mapa", "/historico", "/bula", "/x")]
    monkeypatch.setattr(main, "barra_mobile", lambda _: barra_mobile(itens))
    html = cliente.get("/visao-geral").text
    assert "<summary>Mais</summary>" in html and html.count('aria-current="page"') == 1


# --- R1: 404 em HTML para telas; JSON para API e parciais -----------------------------------------
def test_tela_futura_mostra_em_construcao_no_layout(cliente, menu_com_tela_futura):
    r = cliente.get("/em-breve")
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
    assert '<nav class="menu"' in r.text and "em construção" in r.text and "<h1>Em breve</h1>" in r.text


def test_endereco_desconhecido_mostra_pagina_nao_encontrada(cliente):
    r = cliente.get("/nao-existe")
    assert r.status_code == 404 and "Página não encontrada" in r.text and 'href="/visao-geral"' in r.text


@pytest.mark.parametrize("rota", ["/api/nao-existe", "/parcial/nao-existe", "/static/nao-existe.js"])
def test_api_e_parciais_seguem_em_json(cliente, rota):
    r = cliente.get(rota)
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_404_com_banco_fora_nao_vira_500(cliente, monkeypatch, menu_com_tela_futura):
    def cai():
        raise psycopg.OperationalError("banco fora")

    monkeypatch.setattr(dados, "listar_fazendas", cai)
    r = cliente.get("/em-breve")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


@pytest.mark.parametrize("cabecalhos", [{}, {"HX-Request": "true"}])
def test_404_levantado_pela_rota_segue_em_json(cliente, cabecalhos):
    """OS inexistente em /os/{id}/concluir (G7, pedido do HTMX) e PDF inexistente: o motivo da rota chega
    como está; a página "não encontrada" no layout é só para endereço sem rota."""
    r = cliente.get("/os/999/concluir", headers=cabecalhos)
    assert r.status_code == 404 and r.json() == {"detail": "OS não encontrada ou já concluída"}
    assert cliente.get("/relatorios/999/pdf").json() == {"detail": "relatório não encontrado"}


def test_pedido_do_htmx_para_endereco_sem_rota_segue_em_json(cliente):
    r = cliente.get("/nao-existe", headers={"HX-Request": "true"})
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}


def test_422_continua_em_json(cliente):
    assert cliente.post("/aprovacao/7/3/TALVEZ").status_code == 422


# --- M1 / M3: mapa ---------------------------------------------------------------------------------
def test_mapa_tem_aviso_para_monitor_fora_e_sem_previsao(cliente):
    assert 'id="mapa-aviso"' in cliente.get("/mapa").text
    js = cliente.get("/static/mapa.js").text
    assert "Não consegui falar com o Monitor" in js and "Ainda não há previsão" in js
    assert "scrollIntoView" in js


def test_detalhe_do_mapa_usa_selo_e_mono(cliente, monkeypatch):
    monkeypatch.setattr(main, "cliente_http", lambda: httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json=[]))))
    inicio = datetime.now(timezone.utc) + timedelta(hours=3)
    monkeypatch.setattr(dados, "ordens_do_talhao", lambda t, de, ate: [
        {"id": 1, "tipo_operacao": "pulverizacao", "inicio_previsto": inicio, "status": "suspensa_preventiva",
         "produto": "Abacus HC"}])
    html = cliente.get("/parcial/mapa/talhao/1").text
    assert '<span class="selo atencao">Suspensa</span>' in html and '<span class="mono">' in html


# --- A4 / A8 / A9 ---------------------------------------------------------------------------------
def test_tabela_de_os_vira_cartoes_no_celular(cliente):
    html = cliente.get("/relatorios").text
    assert '<div class="tabela empilha">' in html
    assert 'data-rotulo="Status"' in cliente.get("/parcial/os").text


@pytest.mark.parametrize("rota", TELAS)
def test_layout_tem_favicon_sem_url_externa(cliente, rota):
    html = cliente.get(rota).text
    assert '<link rel="icon" href="/static/icones/logo.svg">' in html  # local: nada de URL externa


# --- M5: a linha do KPI de perda marca a simulação -----------------------------------------------
def test_seletor_marca_a_origem_do_incidente_e_o_js_prefixa_simulacao(cliente):
    live = novo_incidente()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        simulado = conn.execute(
            "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status, origem)"
            " VALUES (2, '[]', '[]', 'aberto', 'replay') RETURNING id").fetchone()[0]
    html = cliente.get("/parcial/seletor").text
    assert re.search(rf'data-id="{live}"[^>]*data-origem="live"', html)
    assert re.search(rf'data-id="{simulado}"[^>]*data-origem="replay"', html)
    assert "· simulação" in cliente.get(f"/incidente/{simulado}").text
