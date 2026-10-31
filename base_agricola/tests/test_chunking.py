import re
from pathlib import Path

import pytest

from base_agricola.chunking import dividir_em_trechos
from base_agricola.manifesto import Fonte, carregar_manifesto
from base_agricola.pdf import PaginaTexto, extrair_paginas

DADOS = Path(__file__).resolve().parents[1] / "dados"


def fonte(tipo_doc="bula", produto="Fungicida X"):
    return Fonte(id="doc-x", titulo="X", tipo_doc=tipo_doc, produto=produto, culturas=["soja"],
                 arquivo=Path("x.pdf"), url="https://x")


def test_secao_continua_na_pagina_seguinte():
    paginas = [PaginaTexto(pagina=1, texto="CONDIÇÕES CLIMÁTICAS\nVento até 10 km/h."),
               PaginaTexto(pagina=2, texto="Não aplicar com chuva prevista em 4 horas.")]
    trechos = dividir_em_trechos(fonte(), paginas)
    assert (trechos[-1].pagina, trechos[-1].secao) == (2, "condicoes_climaticas")


def test_cabecalhos_reconhecidos_sem_acento_e_variantes():
    texto = ("Condicoes meteorologicas\nUmidade acima de 60%.\n"
             "LIMITAÇÕES DE USO:\nNão aplicar com chuva em 48 horas.")
    trechos = dividir_em_trechos(fonte(), [PaginaTexto(pagina=1, texto=texto)])
    assert [t.secao for t in trechos] == ["condicoes_climaticas", "limitacoes_uso"]


def test_cabecalho_desconhecido_encerra_a_secao_anterior():
    texto = "CONDIÇÕES METEOROLÓGICAS\nVento até 10 km/h.\nLIMPEZA DE TANQUE\nLavar o tanque."
    trechos = dividir_em_trechos(fonte(), [PaginaTexto(pagina=1, texto=texto)])
    assert [t.secao for t in trechos] == ["condicoes_climaticas", "outra"]


def test_trecho_respeita_tamanho_e_pagina():
    trechos = dividir_em_trechos(fonte(), [PaginaTexto(pagina=1, texto="\n\n".join(["x" * 500] * 6))])
    assert len(trechos) > 1 and all(len(t.texto) <= 500 and t.pagina == 1 for t in trechos)


def test_documento_nao_bula_usa_geral():
    pagina = PaginaTexto(pagina=1, texto="CONDIÇÕES CLIMÁTICAS\nx")
    trechos = dividir_em_trechos(fonte("fenologia", None), [pagina])
    assert {(t.secao, t.produto) for t in trechos} == {("geral", None)}


def test_ordem_sequencial_no_documento():
    paginas = [PaginaTexto(pagina=n, texto=f"texto {n}") for n in (1, 2, 3)]
    assert [t.ordem for t in dividir_em_trechos(fonte(), paginas)] == [0, 1, 2]


LIMITES = {
    "Abacus HC": (7, "condicoes_climaticas", r"quatro \(4\) horas"),
    "Ativum": (6, "condicoes_climaticas", r"quatro \(4\) horas"),
    "Brucia": (9, "limitacoes_uso", r"chuva em até 48 horas"),
    "Amplexus": (5, "modo_aplicacao", r"quatro \(4\) horas"),
}


@pytest.mark.parametrize("produto", LIMITES)
def test_bulas_reais_trecho_do_limite_de_chuva(produto):
    [f] = [f for f in carregar_manifesto(DADOS / "fontes.yaml") if f.produto == produto]
    pagina, secao, padrao = LIMITES[produto]
    trechos = dividir_em_trechos(f, extrair_paginas(f.arquivo))
    achados = [t for t in trechos if re.search(padrao, " ".join(t.texto.split()))]
    assert achados, "nenhum trecho com o limite de chuva"
    assert (achados[0].pagina, achados[0].secao, achados[0].produto) == (pagina, secao, produto)


FENOLOGIA = {"fenologia-milho": ("milho", "V6", 8), "fenologia-soja": ("soja", "R1", 4)}


@pytest.mark.parametrize("doc_id", FENOLOGIA)
def test_fenologia_real_traz_o_estagio_da_fazenda(doc_id):
    [f] = [f for f in carregar_manifesto(DADOS / "fontes.yaml") if f.id == doc_id]
    cultura, estagio, pagina = FENOLOGIA[doc_id]
    trechos = dividir_em_trechos(f, extrair_paginas(f.arquivo))
    assert f.culturas == [cultura] and {(t.secao, t.produto) for t in trechos} == {("geral", None)}
    assert any(estagio in t.texto and t.pagina == pagina for t in trechos)
