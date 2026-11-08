"""Modelos de dados que circulam no estado da sessão e entre os nós.

No estado da sessão tudo é guardado como JSON (`model_dump(mode="json")`), porque o
`DatabaseSessionService` persiste o estado em Postgres.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, Field, model_validator

# G6: `rajada` (km/h), `delta_t` (°C) e `inversao` (1 = inversão térmica prevista; o limite citado é 0,
# "deve ser evitada") vêm dos campos opcionais do contrato 21 (E3).
Parametro = Literal["chuva", "vento", "umidade", "temperatura", "rajada", "delta_t", "inversao"]
# Sentido do limite: "maximo" viola acima dele, "minimo" viola abaixo ("vento entre 2 e 10 km/h" tem os
# dois). Sem sentido explícito vale o padrão do parâmetro (`SENTIDO_PADRAO`).
Sentido = Literal["maximo", "minimo"]
TipoInsumo = Literal["fungicida", "herbicida", "inseticida"]  # D2, contrato 15
RespostaTipo = Literal["SIM", "NAO", "OPCOES"]
StatusIncidente = Literal["aberto", "aguardando_aprovacao", "aguardando_decisao_manual", "encerrado"]

# Faixas plausíveis por parâmetro, nas unidades do contrato 12. Valor fora delas indica
# unidade trocada (vento em m/s, por exemplo) ou número inventado.
FAIXAS: dict[str, tuple[float, float]] = {
    "chuva": (0, 500),
    "vento": (0, 200),
    "umidade": (0, 100),
    "temperatura": (-10, 60),
    "rajada": (0, 250),
    "delta_t": (0, 30),
    "inversao": (0, 1),
}
SENTIDO_PADRAO: dict[str, Sentido] = {"umidade": "minimo"}


class HoraPrevisao(BaseModel):
    ts: AwareDatetime
    chuva_mm: float = 0
    vento_kmh: float = 0
    umidade_pct: float = 0
    temp_c: float = 0
    # Contrato 21 (E3): opcionais. `None` = o Monitor não mandou; o `agents` funciona sem eles.
    vento_dir_graus: float | None = None
    rajada_kmh: float | None = None
    delta_t_c: float | None = None
    inversao: bool | None = None


class Gatilho(BaseModel):
    # Texto livre: o Monitor ganha tipos novos com o E7 (contrato 21) e um tipo desconhecido não pode
    # impedir o `agents` de carregar o incidente.
    tipo: str
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
    sentido: Sentido | None = Field(
        None,
        description='"maximo" (viola acima do limite) ou "minimo" (viola abaixo). Padrão: mínimo para'
        " umidade, máximo para os outros",
    )
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
        if self.parametro == "inversao" and (self.limite != 0 or self.sentido_efetivo != "maximo"):
            raise ValueError("inversão térmica só tem o limite 0 (deve ser evitada)")
        return self

    @property
    def sentido_efetivo(self) -> Sentido:
        return self.sentido or SENTIDO_PADRAO.get(self.parametro or "", "maximo")


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


class CustoAtraso(BaseModel):
    """G5: quanto custa o gerente demorar a responder, calculado em código quando o plano é proposto.

    A janela proposta faz parte de um bloco de inícios válidos seguidos; ela "vence" quando o último
    início do bloco fica a menos da antecedência mínima. Perdida, a OS vai para a próxima janela boa.
    """

    os_id: int
    talhao: str
    valor_aplicacao_rs: float | None = Field(None, description="área × dose × preço")
    fator_dia: float | None = Field(None, description="Fração do valor perdida por dia de atraso")
    fonte_fator: str
    janela_proposta: AwareDatetime
    vence_em: AwareDatetime
    proxima_janela: AwareDatetime | None = None
    custo_perder_janela_rs: float | None = Field(
        None, description="valor × fator × dias entre a janela proposta e a próxima"
    )


class CitacaoBula(BaseModel):
    trecho: str = Field(description="Texto literal de um trecho devolvido por buscar_na_bula")
    fonte_doc: str
    pagina: int
    produto: str | None = None


class RespostaBula(BaseModel):
    """G4: resposta do Pergunte à bula. Com `encontrou=True`, ao menos uma citação é obrigatória."""

    encontrou: bool = Field(description="false quando nenhum trecho recuperado responde à pergunta")
    resposta: str = Field(description="Resposta curta, em português, só com o que os trechos dizem")
    citacoes: list[CitacaoBula] = []

    @model_validator(mode="after")
    def _exige_citacao(self) -> Self:
        if self.encontrou and not self.citacoes:
            raise ValueError("resposta sem citação")
        return self


class ResultadoBula(BaseModel):
    """O que a API devolve: a resposta já conferida em código (`agents.rules.citacao`)."""

    encontrou: bool
    resposta: str
    citacoes: list[CitacaoBula] = []
    descartadas: int = Field(0, description="Citações do LLM que não vieram da busca")
    erro: str | None = None


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
