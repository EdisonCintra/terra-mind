import os
from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel


class HoraPrevisao(BaseModel):
    ts: AwareDatetime
    # None = o Open-Meteo não mandou o valor da hora (contrato 21: ausente é None, nunca 0)
    chuva_mm: float | None
    vento_kmh: float | None
    umidade_pct: float | None
    temp_c: float | None
    # Opcionais (contrato 21): ausência no Open-Meteo vira None, nunca 0
    vento_dir_graus: float | None = None
    rajada_kmh: float | None = None
    delta_t_c: float | None = None
    inversao: bool | None = None


    @property
    def completa(self) -> bool:
        """Tem os quatro campos básicos; o `agents` exige todos (sem eles assumiria 0)."""
        return None not in (self.chuva_mm, self.vento_kmh, self.umidade_pct, self.temp_c)


class DiaPrevisao(BaseModel):
    data: date
    chuva_mm: float
    prob_chuva_pct: float | None = None
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    vento_max_kmh: float | None = None
    rajada_max_kmh: float | None = None
    vento_dir_graus: float | None = None


class Previsao(BaseModel):
    horas: list[HoraPrevisao]
    dias: list[DiaPrevisao] = []


class Limiares(BaseModel):
    chuva_mm: float = 20
    vento_kmh: float = 11  # a 2 m, como o snapshot (`fontes.vento_2m`); equivale a 15 km/h a 10 m
    umidade_min_pct: float = 50
    janela_h: int = 48
    # Padrões de docs/pesquisa/condicoes-aplicacao.md (E7).
    rajada_kmh: float = 15  # a 2 m, como o vento; equivale à estimativa de 20 km/h a 10 m (E7)
    delta_t_min_c: float = 2
    delta_t_max_c: float = 8
    inversao_vento_max_kmh: float = 2

    @classmethod
    def do_ambiente(cls) -> "Limiares":
        nomes = {"chuva_mm": "LIMIAR_CHUVA_MM", "vento_kmh": "LIMIAR_VENTO_KMH",
                 "umidade_min_pct": "LIMIAR_UMIDADE_MIN_PCT", "janela_h": "JANELA_RISCO_H",
                 "rajada_kmh": "LIMIAR_RAJADA_KMH",
                 "delta_t_min_c": "LIMIAR_DELTA_T_MIN_C", "delta_t_max_c": "LIMIAR_DELTA_T_MAX_C",
                 "inversao_vento_max_kmh": "LIMIAR_INVERSAO_VENTO_MAX_KMH"}
        return cls(**{campo: os.environ[env] for campo, env in nomes.items() if env in os.environ})


class Gatilho(BaseModel):
    tipo: Literal["chuva", "vento", "umidade", "rajada", "delta_t", "inversao"]
    valor: float
    limiar: float
    inicio: AwareDatetime
    fim: AwareDatetime


class Talhao(BaseModel):
    id: int
    nome: str
    lat: float
    lon: float


class ResultadoCiclo(BaseModel):
    incidentes: list[int]
    erros: list[str]
    executado_em: AwareDatetime


class Estacao(BaseModel):
    codigo: str
    nome: str
    lat: float
    lon: float
    fonte: Literal["inmet", "fazenda"]


class LeituraObservada(BaseModel):
    """Clima que de fato ocorreu numa hora; campo sem medição é None, nunca 0."""

    ts: AwareDatetime
    chuva_mm: float | None = None
    temp_c: float | None = None
    umidade_pct: float | None = None
    vento_kmh: float | None = None
    vento_dir_graus: float | None = None
    fonte: Literal["estacao", "reanalise"]
    estacao: str | None = None
    distancia_km: float | None = None
