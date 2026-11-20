"""Telas de perda evitada (G1), agenda e janela da OS (G2 e G3) e relatórios de aplicação (G7)."""

import asyncio
import json
from datetime import datetime, timedelta

import httpx
import psycopg
import pymupdf
import pytest
from fastapi.testclient import TestClient

from agents.clientes.erp import ErpIndisponivel
from agents.clientes.repositorio import RepositorioPostgres
from agents.config import Settings
from agents.deps import Dependencias
from agents.modelos import HoraPrevisao
from agents.relatorio import ServicoRelatorio
from agents.simulacao import FakeCanal, FakeClima, FakeConhecimento
from erp_mcp import leitura
from painel import dados, escrita, main
from tests.conftest import DATABASE_URL
from tests.test_painel_telas import http_falso, novo_incidente, sql

AGORA = datetime.now(dados.FUSO).replace(minute=0, second=0, microsecond=0)


@pytest.fixture
def cliente():
    return TestClient(main.app)


# ------------------------------------------------------------------ G1: perda evitada
def impacto(incidente_id, os_id, perda, situacao="evitada", fonte="estimativa: chuva lava o produto"):
    sql(
        "INSERT INTO agente.impactos (incidente_id, os_id, talhao_id, produto, condicao,"
        " valor_aplicacao_rs, fator, fonte, perda_rs, situacao)"
        " SELECT %s, os.id, os.talhao_id, 'Abacus HC', 'chuva', %s, 0.5, %s, %s, %s"
        " FROM erp.ordens_servico os WHERE os.id = %s",
        (incidente_id, None if perda is None else perda * 2, fonte, perda, situacao, os_id),
    )


def test_perda_soma_so_o_que_foi_evitado_e_deixa_o_replay_de_fora(cliente):
    real = novo_incidente("encerrado")
    simulado = novo_incidente("encerrado")
    sql("UPDATE agente.incidentes SET origem = 'replay' WHERE id = %s", (simulado,))
    impacto(real, 1, 3330.00)
    impacto(real, 2, 1200.50, situacao="suspensa")
    impacto(simulado, 3, 9999.00)

    r = cliente.get("/perda")
    assert r.status_code == 200
    assert "R$ 3.330,00" in r.text and "R$ 1.200,50" in r.text  # evitada e em aberto
    assert "9.999" not in r.text and "inclui estimativa" in r.text
    assert "Talhão 1 - Sede" in r.text and "Abacus HC" in r.text

    com_replay = cliente.get("/perda", params={"replay": 1})
    assert "R$ 13.329,00" in com_replay.text and "simulação" in com_replay.text


def test_perda_bate_com_os_planos_e_mostra_o_tempo_de_resposta(cliente):
    incidente = novo_incidente("encerrado")
    sql(
        "INSERT INTO agente.planos (incidente_id, opcoes_json, aprovado_por, criado_em, respondido_em)"
        " VALUES (%s, '{}', 'gerente', now() - interval '3 hours', now() - interval '1 hour'),"
        "        (%s, '{}', NULL, now() - interval '2 hours', now() - interval '1 hour'),"
        "        (%s, '{}', NULL, now(), NULL)",
        (incidente, incidente, incidente),
    )
    p = dados.perda_evitada(AGORA.date() - timedelta(days=1), AGORA.date())
    assert p["planos"] | {"aprovados": 1, "nao_aprovados": 1, "pendentes": 1} == p["planos"]
    assert p["planos"]["tempo_resposta"] == "1 h"  # média de 2 h e 1 h, arredondada para baixo
    assert p["total"] == "R$ 0,00" and p["retorno"] is None


def test_os_sem_fator_nao_entra_no_total_e_e_avisada(cliente):
    incidente = novo_incidente("encerrado")
    impacto(incidente, 1, None, fonte="sem fator cadastrado")
    r = cliente.get("/perda")
    assert "R$ 0,00" in r.text and "1 OS sem fator de perda cadastrado" in r.text and "sem fator" in r.text


def test_retorno_sobre_a_assinatura_so_com_o_preco_configurado(cliente, monkeypatch):
    impacto(novo_incidente("encerrado"), 1, 4110.00)
    assert "Retorno na safra" not in cliente.get("/perda").text
    monkeypatch.setenv("PRECO_HA_SAFRA", "10")
    cliente.cookies.set("fazenda", "1")  # a assinatura é por fazenda: sem escolher, somaria a Demo também
    r = cliente.get("/perda")  # 205,5 ha × R$ 10 = R$ 2.055,00 de assinatura
    assert "Retorno na safra" in r.text and "2,0×" in r.text and "R$ 2.055,00" in r.text


def test_inicio_da_safra_e_julho_ou_o_configurado(monkeypatch):
    assert dados.inicio_da_safra(datetime(2026, 11, 16).date()).isoformat() == "2026-07-01"
    assert dados.inicio_da_safra(datetime(2027, 3, 1).date()).isoformat() == "2026-07-01"
    monkeypatch.setenv("SAFRA_INICIO", "2026-09-15")
    assert dados.inicio_da_safra(datetime(2026, 11, 16).date()).isoformat() == "2026-09-15"


# ------------------------------------------------------------------ G2 e G3: agenda e janela
def faixa(dia, de, ate, serve, **extra):
    return {
        "inicio": dia.replace(hour=de).isoformat(),
        "fim": dia.replace(hour=ate).isoformat(),
        "serve": serve,
        **extra,
    }


def agenda_do_agents(bula_lida=True):
    dia = AGORA + timedelta(days=1)
    ruim = {"motivo": "chuva prevista de 12.0 mm acima do limite de 0 mm", "parametro": "chuva:maximo"}
    return {
        "insumo_id": 1,
        "produto": "Abacus HC",
        "duracao_horas": 8,
        "inicio": AGORA.date().isoformat(),
        "bula_lida": bula_lida,
        "fonte_previsao": "Open-Meteo",
        "erros": [],
        "limites": [
            {"parametro": "chuva", "sentido": "maximo", "limite": 0, "unidade": "mm", "janela_h": 4,
             "trecho": "chuvas dentro de quatro (4) horas", "fonte_doc": "bula-abacus-hc", "pagina": 7,
             "generico": False},
            {"parametro": "vento", "sentido": "maximo", "limite": 10, "unidade": "km/h", "janela_h": None,
             "trecho": None, "fonte_doc": None, "pagina": None, "generico": True},
        ],
        "talhoes": [
            {
                "talhao_id": 1,
                "talhao": "Talhão 1 - Sede",
                "cultura": "milho",
                "dias": [
                    {
                        "data": dia.date().isoformat(),
                        "horas_boas": 4,
                        "faixas": [
                            faixa(dia, 6, 10, True),
                            faixa(dia, 10, 19, False, fonte="bula, p. 7", pagina=7, **ruim),
                        ],
                    }
                ],
            }
        ],
    }  # fmt: skip


def test_tela_da_agenda_lista_produtos_e_talhoes_do_erp(cliente):
    r = cliente.get("/agenda")
    assert r.status_code == 200 and 'hx-get="/parcial/agenda"' in r.text
    assert "Abacus HC" in r.text and "Talhão 2 - Baixada" in r.text and "Criar OS" in r.text


def test_agenda_mostra_horas_boas_e_o_motivo_com_a_pagina_da_bula(cliente, monkeypatch):
    vistas = http_falso(monkeypatch, lambda req: httpx.Response(200, json=agenda_do_agents()))
    r = cliente.get("/parcial/agenda", params={"insumo_id": 1, "duracao_horas": 4})

    assert vistas[0].url.path == "/agenda" and vistas[0].url.params["duracao_horas"] == "4.0"
    assert (
        r.text.count('class="hora boa"') == 4 + 1 and r.text.count('class="hora ruim"') == 9 + 1
    )  # + legenda
    assert "10h–19h: chuva prevista de 12.0 mm acima do limite de 0 mm (bula, p. 7)" in r.text
    assert "bula, p. 7" in r.text and "referência genérica" in r.text
    assert "Ler os limites da bula" not in r.text


def test_agenda_sem_bula_lida_oferece_a_leitura_e_falha_do_agents_vira_mensagem(cliente, monkeypatch):
    http_falso(monkeypatch, lambda req: httpx.Response(200, json=agenda_do_agents(bula_lida=False)))
    assert "Ler os limites da bula" in cliente.get("/parcial/agenda", params={"insumo_id": 1}).text
    http_falso(monkeypatch, lambda req: httpx.Response(503, json={"detail": "previsão indisponível: x"}))
    r = cliente.get("/parcial/agenda", params={"insumo_id": 1})
    assert r.status_code == 200 and "previsão indisponível" in r.text and "mensagem-erro" in r.text


def test_ler_limites_da_bula_avisa_a_agenda(cliente, monkeypatch):
    limites = [{"parametro": "chuva", "pagina": 7}, {"parametro": "vento", "pagina": 8}]
    vistas = http_falso(
        monkeypatch, lambda req: httpx.Response(200, json={"produto": "Abacus HC", "limites": limites})
    )
    r = cliente.post("/agenda/limites", data={"produto": "Abacus HC"})
    assert json.loads(vistas[0].content) == {"produto": "Abacus HC"}
    assert "2 limite(s) lido(s)" in r.text and "p. 7, p. 8" in r.text
    assert r.headers["HX-Trigger"] == "agenda-mudou"

    http_falso(monkeypatch, lambda req: httpx.Response(200, json={"produto": "X", "limites": []}))
    assert "não traz limite de clima" in cliente.post("/agenda/limites", data={"produto": "X"}).text


def test_janela_ruim_avisa_cita_a_bula_e_oferece_sugestao_que_preenche_o_campo(cliente, monkeypatch):
    sugerido = (AGORA + timedelta(days=2)).replace(hour=6)
    resposta = {
        "serve": False,
        "motivo": "terça, 14h: chuva prevista de 12.0 mm acima do limite de 0 mm (bula, p. 7)",
        "citacao": {
            "trecho": "chuvas dentro de quatro (4) horas",
            "fonte_doc": "bula-abacus-hc",
            "pagina": 7,
        },
        "fonte_limite": "bula, p. 7",
        "fonte_previsao": "Open-Meteo",
        "sugestoes": [
            {
                "inicio": sugerido.isoformat(),
                "maquina_id": 2,
                "maquina": "Pulverizador B",
                "quando": "quinta às 06:00",
            }
        ],
    }
    vistas = http_falso(monkeypatch, lambda req: httpx.Response(200, json=resposta))
    inicio = (AGORA + timedelta(days=1)).replace(hour=14)
    form = {"talhao_id": 1, "insumo_id": 1, "inicio": f"{inicio:%Y-%m-%dT%H:%M}", "duracao_horas": 4}
    r = cliente.post("/agenda/avaliar", data=form)

    assert json.loads(vistas[0].content) == {
        "talhao_id": 1, "insumo_id": 1, "inicio": inicio.isoformat(), "duracao_horas": 4.0,
    }  # fmt: skip
    assert 'data-serve="nao"' in r.text and "bula-abacus-hc, p. 7" in r.text
    assert f'data-inicio="{sugerido:%Y-%m-%dT%H:%M}"' in r.text and "Pulverizador B" in r.text


def test_janela_boa_traz_as_maquinas_livres_do_erp(cliente, monkeypatch):
    boa = {"serve": True, "motivo": None, "citacao": None, "sugestoes": [], "fonte_previsao": "Open-Meteo"}
    http_falso(monkeypatch, lambda req: httpx.Response(200, json=boa))
    inicio = (AGORA + timedelta(days=3)).replace(hour=8)
    form = {"talhao_id": 1, "insumo_id": 1, "inicio": f"{inicio:%Y-%m-%dT%H:%M}", "duracao_horas": 4}
    r = cliente.post("/agenda/avaliar", data=form)
    assert 'data-serve="sim"' in r.text and 'name="maquina_id"' in r.text
    assert "Pulverizador A" in r.text and "Pulverizador B" in r.text


def test_criar_os_grava_com_auditoria_e_recusa_maquina_ocupada_ou_sem_estoque(cliente):
    inicio = (AGORA + timedelta(days=3)).replace(hour=8)
    form = {
        "talhao_id": 2, "insumo_id": 1, "inicio": f"{inicio:%Y-%m-%dT%H:%M}", "duracao_horas": 4,
        "dose_por_ha": "0.3", "maquina_id": 2, "operador_id": "",
    }  # fmt: skip
    r = cliente.post("/os/nova", data=form)
    assert "OS 4 criada" in r.text and r.headers["HX-Trigger"] == "os-criada"
    nova = sql("SELECT talhao_id, status, maquina_id, inicio_previsto FROM erp.ordens_servico WHERE id = 4")
    assert nova[:3] == (2, "agendada", 2) and nova[3] == inicio
    assert sql("SELECT count(*) FROM agente.auditoria WHERE no = 'painel' AND acao = 'criar_os'")[0] == 1

    repetida = cliente.post("/os/nova", data=form)  # o Pulverizador B agora está na OS 4
    assert "já está na OS 4" in repetida.text and "mensagem-erro" in repetida.text
    sem_estoque = cliente.post("/os/nova", data={**form, "maquina_id": 1, "dose_por_ha": "50"})
    assert "insuficiente" in sem_estoque.text
    assert sql("SELECT count(*) FROM erp.ordens_servico")[0] == 4


# ------------------------------------------------------------------ G7: conclusão e relatório
class ErpDireto:
    """As leituras do MCP chamadas direto, sem o transporte: o mesmo SQL que o `agents` recebe."""

    async def detalhar_execucao(self, os_id):
        resultado = leitura.detalhar_execucao(os_id)
        if resultado.get("ok") is False:
            raise ErpIndisponivel(resultado["erro"])
        return resultado


def agents_de_verdade(monkeypatch, inicio):
    """O `POST /relatorios/os/{id}` do painel cai no `ServicoRelatorio` real, sobre o mesmo Postgres."""
    horas = [
        HoraPrevisao(ts=inicio + timedelta(hours=i), chuva_mm=0, vento_kmh=6, umidade_pct=70, temp_c=24)
        for i in range(-2, 30)
    ]

    def responder(req: httpx.Request) -> httpx.Response:
        async def gerar():
            repo = RepositorioPostgres.de_url(DATABASE_URL)
            deps = Dependencias(
                settings=Settings(_env_file=None),
                erp=ErpDireto(),
                conhecimento=FakeConhecimento(),
                repo=repo,
                canal=FakeCanal(),
                clima=FakeClima(horas=horas),
            )
            try:
                registro = await ServicoRelatorio(deps).gerar(int(req.url.path.rsplit("/", 1)[1]))
            finally:
                await repo.engine.dispose()
            return {**registro, "gerado_em": registro["gerado_em"].isoformat()}

        return httpx.Response(201, json=asyncio.run(gerar()))

    return http_falso(monkeypatch, responder)


def formulario(inicio, **extra):
    return {
        "inicio_real": f"{inicio:%Y-%m-%dT%H:%M}",
        "fim_real": f"{inicio + timedelta(hours=3):%Y-%m-%dT%H:%M}",
        "area_aplicada_ha": "118,5",
        "dose_real_por_ha": "0,3",
        "volume_calda_l_ha": "120",
        "modalidade": "terrestre",
        "receituario_numero": "GO-2026-000123",
        "responsavel_tecnico": "Eng. Agr. Maria Souza",
        "registro_profissional": "CREA-GO 12345",
        **extra,
    }


def test_concluir_a_os_da_demo_gera_o_pdf_com_todos_os_campos(cliente, monkeypatch):
    inicio = AGORA - timedelta(hours=6)
    agents_de_verdade(monkeypatch, inicio)
    assert "Concluir OS 1" in cliente.get("/os/1/concluir").text

    r = cliente.post("/os/1/concluir", data=formulario(inicio))
    assert "OS 1 concluída. Relatório v1 gerado" in r.text and r.headers["HX-Trigger"] == "relatorios-mudou"
    assert sql("SELECT status FROM erp.ordens_servico WHERE id = 1")[0] == "executada"
    execucao = sql("SELECT area_aplicada_ha, maquina_id, operador_id, inicio_real FROM erp.execucoes_os")
    assert (float(execucao[0]), execucao[1], execucao[2], execucao[3]) == (118.5, 1, 1, inicio)
    assert sql("SELECT count(*) FROM agente.auditoria WHERE no = 'painel' AND acao = 'concluir_os'")[0] == 1

    lista = cliente.get("/relatorios")
    assert "PDF v1" in lista.text and "Não conferida" in lista.text  # a bula ainda não foi lida
    relatorio_id, sha = sql("SELECT id, sha256 FROM agente.relatorios_aplicacao WHERE os_id = 1")
    pdf = cliente.get(f"/relatorios/{relatorio_id}/pdf")
    assert pdf.headers["content-type"] == "application/pdf"
    with pymupdf.open(stream=pdf.content, filetype="pdf") as doc:
        texto = " ".join(" ".join(pagina.get_text().split()) for pagina in doc)
    for esperado in (
        "Talhão 1 - Sede", "milho", "Abacus HC", "118,50 ha", "0,300 L/ha", "Pulverizador A", "João",
        "GO-2026-000123", "Eng. Agr. Maria Souza", "CREA-GO 12345", f"{inicio:%d/%m/%Y %H:%M}",
        "24,0 a 24,0 °C", "6,0 a 6,0 km/h", sha,
    ):  # fmt: skip
        assert esperado in texto, esperado
    assert "não informado" not in texto.split("CLIMA DURANTE")[0].replace(
        "Ponta de pulverização não informado", ""
    )


def test_relatorio_fica_conforme_quando_a_bula_foi_lida_e_gerar_de_novo_cria_versao(cliente, monkeypatch):
    inicio = AGORA - timedelta(hours=30)
    agents_de_verdade(monkeypatch, inicio)
    cliente.post("/os/1/concluir", data=formulario(inicio))
    sql(
        "INSERT INTO conhecimento.limites_produto (produto, parametro, sentido, limite, unidade, janela_h,"
        " trecho, documento, pagina) VALUES ('Abacus HC', 'chuva', 'maximo', 0, 'mm', 4,"
        " 'chuvas dentro de um período de quatro (4) horas', 'bula-abacus-hc', 7)"
    )
    r = cliente.post("/relatorios/os/1/gerar")
    assert "versão 2" in r.text
    assert sql("SELECT conforme FROM agente.relatorios_aplicacao WHERE os_id = 1 AND versao = 2")[0] is True
    assert (
        "PDF v2" in cliente.get("/parcial/relatorios").text and "Conforme" in cliente.get("/relatorios").text
    )


def test_os_suspensa_ou_com_termino_no_futuro_nao_e_concluida(cliente, monkeypatch):
    http_falso(monkeypatch, lambda req: httpx.Response(500))
    inicio = AGORA - timedelta(hours=6)
    futuro = formulario(inicio, fim_real=f"{AGORA + timedelta(hours=5):%Y-%m-%dT%H:%M}")
    assert "não pode estar no futuro" in cliente.post("/os/1/concluir", data=futuro).text
    sql("UPDATE erp.ordens_servico SET status = 'suspensa_preventiva' WHERE id = 1")
    r = cliente.post("/os/1/concluir", data=formulario(inicio))
    assert "não pode ser concluída (status atual: suspensa_preventiva)" in r.text
    assert sql("SELECT count(*) FROM erp.execucoes_os")[0] == 0


def test_falha_do_relatorio_nao_perde_a_execucao(cliente, monkeypatch):
    http_falso(monkeypatch, lambda req: httpx.Response(409, json={"detail": "clima fora do ar"}))
    r = cliente.post("/os/1/concluir", data=formulario(AGORA - timedelta(hours=6)))
    assert (
        "mensagem-aviso" in r.text and "Execução da OS 1 gravada" in r.text and "clima fora do ar" in r.text
    )
    assert sql("SELECT count(*) FROM erp.execucoes_os")[0] == 1
    assert "Sem relatório" in cliente.get("/relatorios").text


def test_concluir_valida_modalidade_direto_na_escrita():
    r = escrita.concluir_os(1, {"modalidade": "barco", "inicio_real": AGORA, "fim_real": AGORA})
    assert r == {"ok": False, "erro": "Modalidade inválida."}


def test_pdf_inexistente_e_404(cliente):
    assert cliente.get("/relatorios/999/pdf").status_code == 404
    with psycopg.connect(DATABASE_URL) as conn:
        assert conn.execute("SELECT count(*) FROM agente.relatorios_aplicacao").fetchone()[0] == 0
