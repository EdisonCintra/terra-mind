from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from base_agricola.manifesto import Fonte, carregar_manifesto

DADOS = Path(__file__).resolve().parents[1] / "dados"


def escrever_yaml(tmp_path, fontes):
    caminho = tmp_path / "fontes.yaml"
    caminho.write_text(yaml.safe_dump({"fontes": fontes}, allow_unicode=True), encoding="utf-8")
    return caminho


def fonte(**campos):
    base = {"id": "bula-x", "titulo": "Bula X", "tipo_doc": "bula", "produto": "Fungicida X",
            "culturas": ["soja"], "arquivo": "docs/x.pdf", "url": "https://x"}
    return base | campos


def test_carrega_fonte_valida(tmp_path):
    [f] = carregar_manifesto(escrever_yaml(tmp_path, [fonte()]))
    assert f.produto == "Fungicida X" and f.arquivo == tmp_path / "docs" / "x.pdf"


def test_bula_sem_produto_invalida(tmp_path):
    with pytest.raises(ValidationError):
        carregar_manifesto(escrever_yaml(tmp_path, [fonte(produto=None)]))


def test_fenologia_com_produto_invalida(tmp_path):
    with pytest.raises(ValidationError):
        carregar_manifesto(escrever_yaml(tmp_path, [fonte(tipo_doc="fenologia")]))


def test_id_repetido_invalido(tmp_path):
    with pytest.raises(ValueError, match="bula-x"):
        carregar_manifesto(escrever_yaml(tmp_path, [fonte(), fonte()]))


def test_manifesto_real_aponta_para_pdfs_existentes():
    fontes = carregar_manifesto(DADOS / "fontes.yaml")
    bulas = {f.produto for f in fontes if f.tipo_doc == "bula"}
    assert bulas == {"Abacus HC", "Ativum", "Brucia", "Amplexus"}
    assert all(isinstance(f, Fonte) and f.arquivo.is_file() for f in fontes)
