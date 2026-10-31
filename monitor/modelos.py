from typing import Literal

from pydantic import AwareDatetime, BaseModel


class HoraPrevisao(BaseModel):
    ts: AwareDatetime
    chuva_mm: float
    vento_kmh: float
    umidade_pct: float
    temp_c: float


class Limiares(BaseModel):
    chuva_mm: float = 20
    vento_kmh: float = 15
    umidade_min_pct: float = 50
    janela_h: int = 48

    @classmethod
    def do_ambiente(cls) -> "Limiares":
        import os

        nomes = {"chuva_mm": "LIMIAR_CHUVA_MM", "vento_kmh": "LIMIAR_VENTO_KMH",
                 "umidade_min_pct": "LIMIAR_UMIDADE_MIN_PCT", "janela_h": "JANELA_RISCO_H"}
        return cls(**{campo: os.environ[env] for campo, env in nomes.items() if env in os.environ})


class Gatilho(BaseModel):
    tipo: Literal["chuva", "vento", "umidade"]
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
