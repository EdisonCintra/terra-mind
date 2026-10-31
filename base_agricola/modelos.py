from typing import Literal

from pydantic import BaseModel, Field

Condicao = Literal["chuva", "vento", "umidade", "temperatura"]


class FatorPerda(BaseModel):
    tipo_insumo: str
    condicao: Condicao
    fator: float = Field(ge=0, le=1)
    fonte_ou_estimativa: str

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
