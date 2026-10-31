from pathlib import Path

import pymupdf
import pytest

from base_agricola.pdf import extrair_paginas

DOCS = Path(__file__).resolve().parents[1] / "dados" / "docs"


@pytest.fixture
def pdf_duas_paginas(tmp_path):
    caminho = tmp_path / "bula.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "CONDIÇÕES CLIMÁTICAS")
    doc.new_page().insert_text((72, 72), "Não aplicar com chuva prevista.")
    doc.save(caminho)
    return caminho


def test_extrai_paginas_numeradas_a_partir_de_1(pdf_duas_paginas):
    paginas = extrair_paginas(pdf_duas_paginas)
    assert [p.pagina for p in paginas] == [1, 2]
    assert "CONDIÇÕES CLIMÁTICAS" in paginas[0].texto


def test_bula_real_do_abacus_traz_o_limite_de_chuva_na_pagina_7():
    paginas = extrair_paginas(DOCS / "abacus_hc.pdf")
    texto_p7 = " ".join(paginas[6].texto.split())
    assert paginas[6].pagina == 7
    assert "quatro (4) horas após a aplicação" in texto_p7
