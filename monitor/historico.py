"""Conferência das suspensões: a chuva prevista na janela da OS contra a que de fato caiu (E6)."""

import logging
from datetime import datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel

from monitor.modelos import LeituraObservada
from monitor.observado import FonteObservada, FonteObservadaIndisponivel, estacao_mais_proxima

log = logging.getLogger("monitor")

ESPERA_SEM_DADO = timedelta(days=10)  # a reanálise atrasa; passado isso, a hora que falta não vem mais


class Suspensao(BaseModel):
    incidente_id: int
    os_id: int
    talhao_id: int
    inicio_previsto: AwareDatetime  # horário ORIGINAL (da auditoria): reagendar_os sobrescreve a OS
    duracao_horas: float
    parametros: list[str]
    limite_chuva_mm: float | None = None
    janela_h: float | None = None
    limite_fonte: str | None = None
    chuva_prevista_mm: float = 0.0


class Conferencia(BaseModel):
    incidente_id: int
    os_id: int
    janela_inicio: AwareDatetime | None = None
    janela_fim: AwareDatetime | None = None
    chuva_prevista_mm: float | None = None
    chuva_observada_mm: float | None = None
    limite_mm: float | None = None
    limite_fonte: str | None = None
    fonte_observado: Literal["estacao", "reanalise"] | None = None
    estacao: str | None = None
    classe: Literal["correta", "falso_alarme", "sem_dado"]
    motivo: str | None = None


def _hora(ts: datetime) -> datetime:
    return ts.replace(minute=0, second=0, microsecond=0)


def janela(s: Suspensao) -> tuple[datetime, datetime]:
    """[início previsto, início + duração da OS + janela de chuva da bula)."""
    return s.inicio_previsto, s.inicio_previsto + timedelta(hours=s.duracao_horas + (s.janela_h or 0))


def horas_esperadas(inicio: datetime, fim: datetime) -> list[datetime]:
    """Uma entrada por hora cheia que toca a janela [inicio, fim)."""
    horas, h = [], _hora(inicio)
    while h < fim:
        horas.append(h)
        h += timedelta(hours=1)
    return horas


def _cobertas(esperadas: list[datetime], observado: list[LeituraObservada]) -> dict[datetime, float] | None:
    """Chuva por hora esperada, ou None se alguma hora falta (ou veio sem medida de chuva)."""
    por_hora = {_hora(l.ts): l.chuva_mm for l in observado if l.chuva_mm is not None}
    if not esperadas or any(h not in por_hora for h in esperadas):
        return None
    return {h: por_hora[h] for h in esperadas}


def cobre(s: Suspensao, observado: list[LeituraObservada]) -> bool:
    inicio, fim = janela(s)
    return _cobertas(horas_esperadas(inicio, fim), observado) is not None


def conferir(s: Suspensao, observado: list[LeituraObservada], agora: datetime) -> Conferencia | None:
    """Classifica a suspensão; None = ainda pendente (nada a gravar). Pura, sem I/O."""
    base = dict(incidente_id=s.incidente_id, os_id=s.os_id)
    if "chuva" not in s.parametros or s.limite_chuva_mm is None or s.janela_h is None:
        return Conferencia(**base, classe="sem_dado", motivo="só chuva é conferida")
    inicio, fim = janela(s)
    comum = dict(**base, janela_inicio=inicio, janela_fim=fim, chuva_prevista_mm=s.chuva_prevista_mm,
                 limite_mm=s.limite_chuva_mm, limite_fonte=s.limite_fonte)
    por_hora = _cobertas(horas_esperadas(inicio, fim), observado)
    if por_hora is None:
        if agora < fim + ESPERA_SEM_DADO:
            return None
        return Conferencia(**comum, classe="sem_dado", motivo="sem dado observado")
    total = sum(por_hora.values())
    classe = "correta" if total > s.limite_chuva_mm else "falso_alarme"
    primeira = observado[0]
    return Conferencia(**comum, chuva_observada_mm=total, fonte_observado=primeira.fonte,
                       estacao=primeira.estacao, classe=classe)


def _observado_da_suspensao(repo, fonte_obs: FonteObservada | None, s: Suspensao) -> list[LeituraObservada]:
    """Estação (mais próxima, até 50 km) primeiro; a reanálise só entra se a estação não cobrir a janela."""
    inicio, fim = janela(s)
    [talhao] = repo.talhoes([s.talhao_id])
    achada = estacao_mais_proxima(talhao.lat, talhao.lon, repo.estacoes())
    if achada is not None:
        estacao, km = achada
        da_estacao = repo.observacoes_estacao(estacao.codigo, _hora(inicio), fim, km)
        if cobre(s, da_estacao):
            return da_estacao
    if fonte_obs is None:
        return []
    try:
        return fonte_obs.leituras(talhao.lat, talhao.lon, _hora(inicio), fim)
    except FonteObservadaIndisponivel as erro:
        log.warning("conferência: observado indisponível: %s", erro)
        return []


def conferir_pendentes(repo, fonte_obs: FonteObservada | None, agora: datetime) -> int:
    """Confere e grava as suspensões ainda sem conferência; devolve quantas foram gravadas."""
    gravadas = 0
    for s in repo.suspensoes_a_conferir():
        try:
            confere_chuva = "chuva" in s.parametros and s.janela_h is not None
            if confere_chuva and agora < janela(s)[1]:
                continue  # a janela ainda não acabou: nada a conferir
            observado = _observado_da_suspensao(repo, fonte_obs, s) if confere_chuva else []
            conferencia = conferir(s, observado, agora)
            if conferencia is not None:
                repo.gravar_conferencia(conferencia)
                gravadas += 1
        except Exception:  # noqa: BLE001 - uma suspensão com problema não trava as outras
            log.exception("conferência da OS %s (incidente %s) falhou", s.os_id, s.incidente_id)
    return gravadas
