import re
from pathlib import Path

import yaml

from base_agricola.modelos import FatorPerda

TIPOS_INSUMO = {"fungicida", "herbicida", "inseticida"}  # contrato 15
CITACAO = re.compile(r"^(?P<doc>[\w-]+):p\.(?P<pagina>[1-9]\d*)$")
ESTIMATIVA = re.compile(r"^estimativa:\s*\S")


def _validar_fonte(fonte: str, docs_existentes: set[str]) -> None:
    citacao = CITACAO.match(fonte)
    if citacao:
        if citacao["doc"] not in docs_existentes:
            raise ValueError(f"fonte cita documento não indexado: {fonte}")
        return
    if not ESTIMATIVA.match(fonte):
        raise ValueError(f"fonte deve ser '<doc_id>:p.<N>' ou 'estimativa: <justificativa>': {fonte!r}")


def carregar_fatores(caminho: Path, docs_existentes: set[str]) -> list[FatorPerda]:
    """Lê e valida o fatores_perda.yaml. Nenhum fator entra sem fonte citada ou estimativa declarada."""
    itens = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))["fatores"]
    fatores = [FatorPerda(**item) for item in itens]
    vistos: set[tuple[str, str]] = set()
    for f in fatores:
        if f.tipo_insumo not in TIPOS_INSUMO:
            raise ValueError(f"tipo_insumo fora do contrato 15: {f.tipo_insumo}")
        _validar_fonte(f.fonte_ou_estimativa, docs_existentes)
        if (f.tipo_insumo, f.condicao) in vistos:
            raise ValueError(f"fator repetido: {f.tipo_insumo}/{f.condicao}")
        vistos.add((f.tipo_insumo, f.condicao))
    return fatores
