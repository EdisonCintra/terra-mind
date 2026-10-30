"""Modelos de dados que circulam no estado da sessão e entre os nós.

No estado da sessão tudo é guardado como JSON (`model_dump(mode="json")`), porque o
`DatabaseSessionService` persiste o estado em Postgres.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

Parametro = Literal["chuva", "vento", "umidade", "temperatura"]
TipoInsumo = Literal["defensivo", "fertilizante"]
RespostaTipo = Literal["SIM", "NAO", "OPCOES"]
StatusIncidente = Literal["aberto", "aguardando_aprovacao", "aguardando_decisao_manual", "encerrado"]

# Faixas plausíveis por parâmetro, nas unidades do contrato 12. Valor fora delas indica
# unidade trocada (vento em m/s, por exemplo) ou número inventado.
FAIXAS: dict[str, tuple[float, float]] = {
    "chuva": (0, 500),
    "vento": (0, 200),
    "umidade": (0, 100),
    "temperatura": (-10, 60),
}


class HoraPrevisao(BaseModel):
    ts: AwareDatetime
    chuva_mm: float = 0
    vento_kmh: float = 0
    umidade_pct: float = 0
    temp_c: float = 0


class Gatilho(BaseModel):
    tipo: Parametro
    valor: float
    limiar: float
    inicio: AwareDatetime
    fim: AwareDatetime


class Incidente(BaseModel):
    id: int
    talhao_id: int
    status: str
    gatilhos: list[Gatilho]
    horas: list[HoraPrevisao]
    criado_em: AwareDatetime | None = None


class OsEmRisco(BaseModel):
    os_id: int
    talhao_id: int
    talhao: str
    area_ha: float
    cultura: str | None = None
    estagio: str | None = None
    tipo_operacao: str
    insumo_id: int | None = None
    produto: str | None = None
    tipo_insumo: str | None = None
    dose_por_ha: float | None = None
    unidade: str | None = None
    preco_unitario: float | None = None
    inicio_previsto: AwareDatetime
    duracao_horas: float = 8
    maquina_id: int | None = None
    tipo_maquina: str | None = Field(None, description="Tipo de máquina que a operação usa")
    operador_id: int | None = None


class ViolacaoOs(BaseModel):
    """Decisão do Avaliador para uma OS. Com `viola=True`, a citação é obrigatória."""

    os_id: int
    viola: bool
    parametro: Parametro | None = None
    limite: float | None = Field(None, description="Limite lido no documento, na unidade do contrato 12")
    unidade: str | None = None
    janela_h: float | None = Field(
        None, description="Horas após o início da aplicação em que o limite vale (ex.: 24 h sem chuva)"
    )
    valor_previsto: float | None = None
    trecho: str | None = Field(None, description="Trecho literal do documento recuperado por buscar_trechos")
    fonte_doc: str | None = None
    pagina: int | None = None
    justificativa: str

    @model_validator(mode="after")
    def _exige_citacao(self) -> Self:
        if not self.viola:
            return self
        faltando = [
            c for c in ("parametro", "limite", "trecho", "fonte_doc", "pagina") if getattr(self, c) is None
        ]
        if faltando:
            raise ValueError(f"violação sem {', '.join(faltando)}")
        minimo, maximo = FAIXAS[self.parametro]
        if not minimo <= self.limite <= maximo:
            raise ValueError(f"limite {self.limite} fora da faixa de {self.parametro} ({minimo}..{maximo})")
        if self.janela_h is not None and not 0 < self.janela_h <= 120:
            raise ValueError("janela_h fora de 0..120 h")
        return self


class AvaliacaoRisco(BaseModel):
    violacoes: list[ViolacaoOs]


class Impacto(BaseModel):
    os_id: int
    area_ha: float
    dose_por_ha: float | None
    preco_unitario: float | None
    fator: float | None
    fonte_fator: str
    perda_rs: float | None


class OpcaoPlano(BaseModel):
    os_id: int
    novo_inicio: AwareDatetime
    maquina_id: int
    justificativa: str


class PlanoProposto(BaseModel):
    opcoes: list[OpcaoPlano] = Field(description="Uma opção por OS suspensa")
    mensagem: str = Field(description="Mensagem curta ao gerente, em português")


class Veredito(BaseModel):
    aprovado: bool
    motivos: list[str] = []


class RespostaGerente(BaseModel):
    resposta: RespostaTipo
    respondido_por: str = "gerente"


@dataclass
class Resposta:
    """Mesmo formato do `channels.base.Resposta` do Felipe (contrato 6)."""

    incidente_id: int
    plano_id: int
    resposta: RespostaTipo
    respondido_por: str


def como_json(modelo: BaseModel | list[BaseModel]) -> dict | list:
    if isinstance(modelo, list):
        return [m.model_dump(mode="json") for m in modelo]
    return modelo.model_dump(mode="json")


def para_datetime(valor: str | datetime) -> datetime:
    return valor if isinstance(valor, datetime) else datetime.fromisoformat(valor)
