from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Condicao = Literal["chuva", "vento", "umidade", "temperatura", "atraso"]


class FatorPerda(BaseModel):
    tipo_insumo: str
    condicao: Condicao
    fator: float = Field(ge=0, le=1)
    fonte_ou_estimativa: str

TipoDoc = Literal["bula", "referencia", "fenologia", "zarc"]


class Fonte(BaseModel):
    """Documento registrado em conhecimento.documentos; `arquivo` já resolvido para um caminho."""

    id: str
    titulo: str
    tipo_doc: TipoDoc
    produto: str | None = None
    culturas: list[str] = []
    arquivo: Path
    url: str | None = None

    @model_validator(mode="after")
    def produto_conforme_tipo(self) -> "Fonte":
        if self.tipo_doc == "bula" and not self.produto:
            raise ValueError(f"{self.id}: bula precisa de produto")
        if self.tipo_doc in ("fenologia", "zarc") and self.produto:
            raise ValueError(f"{self.id}: {self.tipo_doc} não tem produto")
        return self


Secao = Literal[
    "condicoes_climaticas", "modo_aplicacao", "intervalo_seguranca", "limitacoes_uso", "outra", "geral"
]


class Trecho(BaseModel):
    doc_id: str
    produto: str | None
    tipo_doc: str
    culturas: list[str]
    pagina: int
    secao: Secao
    ordem: int
    texto: str


class TrechoEncontrado(BaseModel):
    texto: str
    fonte_doc: str
    pagina: int
    secao: Secao
    produto: str | None
    score: float
