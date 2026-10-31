from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

TipoDoc = Literal["bula", "referencia", "fenologia", "zarc"]


class Fonte(BaseModel):
    id: str
    titulo: str
    tipo_doc: TipoDoc
    produto: str | None = None
    culturas: list[str] = []
    arquivo: Path
    url: str

    @model_validator(mode="after")
    def produto_conforme_tipo(self) -> "Fonte":
        if self.tipo_doc == "bula" and not self.produto:
            raise ValueError(f"{self.id}: bula precisa de produto")
        if self.tipo_doc in ("fenologia", "zarc") and self.produto:
            raise ValueError(f"{self.id}: {self.tipo_doc} não tem produto")
        return self


def carregar_manifesto(caminho: Path) -> list[Fonte]:
    """Lê o fontes.yaml; `arquivo` é relativo à pasta do próprio manifesto."""
    dados = yaml.safe_load(Path(caminho).read_text(encoding="utf-8"))
    fontes = [Fonte(**item) for item in dados["fontes"]]
    repetidos = [id_ for id_, n in Counter(f.id for f in fontes).items() if n > 1]
    if repetidos:
        raise ValueError(f"ids repetidos no manifesto: {', '.join(repetidos)}")
    base = Path(caminho).parent
    return [f.model_copy(update={"arquivo": base / f.arquivo}) for f in fontes]
