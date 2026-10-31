from pathlib import Path

import pytest
import yaml

from base_agricola.fatores import carregar_fatores

DADOS = Path(__file__).resolve().parents[1] / "dados"


def yaml_fatores(tmp_path, fontes, condicao="chuva", tipo_insumo="fungicida"):
    itens = [{"tipo_insumo": tipo_insumo, "condicao": condicao, "fator": 0.3, "fonte_ou_estimativa": f}
             for f in fontes]
    if len(itens) == 2:
        itens[1]["condicao"] = "vento"
    caminho = tmp_path / "fatores.yaml"
    caminho.write_text(yaml.safe_dump({"fatores": itens}, allow_unicode=True), encoding="utf-8")
    return caminho


def test_aceita_citacao_e_estimativa(tmp_path):
    fontes = ["bula-x:p.4", "estimativa: produto lavado pela chuva antes de agir"]
    assert len(carregar_fatores(yaml_fatores(tmp_path, fontes), {"bula-x"})) == 2


@pytest.mark.parametrize("fonte", ["outra-bula:p.1", "", "estimativa: ", "bula-x", "bula-x:p.0"])
def test_rejeita_fonte_invalida(tmp_path, fonte):
    with pytest.raises(ValueError):
        carregar_fatores(yaml_fatores(tmp_path, [fonte]), {"bula-x"})


def test_rejeita_condicao_fora_do_contrato(tmp_path):
    with pytest.raises(ValueError):
        carregar_fatores(yaml_fatores(tmp_path, ["estimativa: x"], condicao="lavagem"), set())


def test_rejeita_tipo_insumo_fora_do_contrato(tmp_path):
    with pytest.raises(ValueError):
        carregar_fatores(yaml_fatores(tmp_path, ["estimativa: x"], tipo_insumo="fertilizante"), set())


def test_rejeita_par_repetido(tmp_path):
    caminho = tmp_path / "f.yaml"
    item = {"tipo_insumo": "fungicida", "condicao": "chuva", "fator": 0.3,
            "fonte_ou_estimativa": "estimativa: x"}
    caminho.write_text(yaml.safe_dump({"fatores": [item, item]}), encoding="utf-8")
    with pytest.raises(ValueError):
        carregar_fatores(caminho, set())


def test_yaml_da_demo_tem_chuva_para_fungicida_e_herbicida():
    ids = {"bula-abacus-hc", "bula-ativum", "bula-brucia", "bula-amplexus"}
    fatores = carregar_fatores(DADOS / "fatores_perda.yaml", ids)
    assert {("fungicida", "chuva"), ("herbicida", "chuva")} <= {(f.tipo_insumo, f.condicao) for f in fatores}
