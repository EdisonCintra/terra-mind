"""Validador em código: confere o plano do Supervisor contra o limite citado e o ERP."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from agents.clientes.erp import ErpCliente, ErpIndisponivel
from agents.modelos import (
    AvaliacaoRisco,
    HoraPrevisao,
    Incidente,
    OsEmRisco,
    PlanoProposto,
    Veredito,
    ViolacaoOs,
    para_datetime,
)
from agents.rules.janelas import janelas_climaticas, motivo_invalida


@dataclass
class ContextoPlano:
    """O que o Validador e as ferramentas de janela precisam, lido do estado da sessão."""

    horas: list[HoraPrevisao]
    os_por_id: dict[int, OsEmRisco]
    violacoes_por_os: dict[int, list[ViolacaoOs]]
    os_suspensas: list[int]
    datas_oferecidas: dict[int, set[datetime]] = field(default_factory=dict)

    @classmethod
    def do_estado(cls, estado: Any) -> "ContextoPlano":
        incidente = Incidente.model_validate(estado["incidente"])
        avaliacao = AvaliacaoRisco.model_validate(estado.get("avaliacao") or {"violacoes": []})
        violacoes: dict[int, list[ViolacaoOs]] = {}
        for v in avaliacao.violacoes:
            if v.viola:
                violacoes.setdefault(v.os_id, []).append(v)
        oferecidas: dict[int, set[datetime]] = {}
        for os_id, datas in (estado.get("datas_oferecidas") or {}).items():
            oferecidas[int(os_id)] = {para_datetime(d) for d in datas}
        return cls(
            horas=incidente.horas,
            os_por_id={o["os_id"]: OsEmRisco.model_validate(o) for o in estado.get("os_em_risco") or []},
            violacoes_por_os=violacoes,
            os_suspensas=list(estado.get("os_suspensas") or []),
            datas_oferecidas=oferecidas,
        )


async def maquinas_livres(erp: ErpCliente, inicio: datetime, os: OsEmRisco) -> set[int]:
    """Máquinas livres durante a OS, só do tipo que a operação usa (quando ele é conhecido)."""
    duracao_h, tipo = os.duracao_horas, os.tipo_maquina
    livres = await erp.disponibilidade_maquinas(inicio, inicio + timedelta(hours=duracao_h), tipo)
    return {int(m.get("id", m.get("maquina_id"))) for m in livres}


async def janelas_validas(
    contexto: ContextoPlano,
    os_id: int,
    erp: ErpCliente,
    agora: datetime,
    max_opcoes: int = 3,
) -> list[dict]:
    """Próximas janelas que passam no clima e têm máquina livre: `[{inicio, maquinas_livres}]`."""
    os = contexto.os_por_id[os_id]
    violacoes = contexto.violacoes_por_os[os_id]
    resultado: list[dict] = []
    for t in janelas_climaticas(
        contexto.horas, violacoes, os.duracao_horas, agora, contexto.datas_oferecidas.get(os_id)
    ):
        livres = await maquinas_livres(erp, t, os)
        if livres:
            preferidas = sorted(livres, key=lambda m: (m != os.maquina_id, m))
            resultado.append({"inicio": t.isoformat(), "maquinas_livres": preferidas})
        if len(resultado) >= max_opcoes:
            break
    return resultado


async def validar(
    plano: PlanoProposto,
    contexto: ContextoPlano,
    erp: ErpCliente,
    agora: datetime,
    exigir_erp: bool = True,
) -> Veredito:
    """Confere cada opção do plano. Com `exigir_erp=False` (revalidação no Executor), o ERP fora do
    ar não invalida a janela: as próprias escritas do ERP conferem a máquina e o plano aprovado."""
    motivos: list[str] = []
    reservas: list[tuple[int, datetime, datetime, int]] = []  # (máquina, início, fim, OS) já aceitas
    por_os = {o.os_id: o for o in plano.opcoes}
    faltando = [i for i in contexto.os_suspensas if i not in por_os]
    if faltando:
        motivos.append(f"faltou opção para as OS {faltando}")
    for opcao in plano.opcoes:
        if opcao.os_id not in contexto.os_suspensas:
            motivos.append(f"OS {opcao.os_id} não está suspensa neste incidente")
            continue
        os = contexto.os_por_id[opcao.os_id]
        violacoes = contexto.violacoes_por_os.get(opcao.os_id)
        if not violacoes:
            motivos.append(f"OS {opcao.os_id} sem avaliação de risco")
            continue
        if opcao.novo_inicio in contexto.datas_oferecidas.get(opcao.os_id, set()):
            motivos.append(f"OS {opcao.os_id}: {opcao.novo_inicio.isoformat()} já foi oferecida ao gerente")
            continue
        motivo = motivo_invalida(opcao.novo_inicio, contexto.horas, violacoes, os.duracao_horas, agora)
        if motivo:
            motivos.append(f"OS {opcao.os_id} em {opcao.novo_inicio.isoformat()}: {motivo}")
            continue
        inicio, fim = opcao.novo_inicio, opcao.novo_inicio + timedelta(hours=os.duracao_horas)
        conflito = next(
            (r for r in reservas if r[0] == opcao.maquina_id and r[1] < fim and inicio < r[2]), None
        )
        if conflito:
            motivos.append(
                f"OS {opcao.os_id}: máquina {opcao.maquina_id} já usada pela OS {conflito[3]}"
                " no mesmo horário"
            )
            continue
        reservas.append((opcao.maquina_id, inicio, fim, opcao.os_id))
        try:
            livres = await maquinas_livres(erp, opcao.novo_inicio, os)
        except ErpIndisponivel as e:
            if exigir_erp:
                motivos.append(f"não consegui conferir a máquina no ERP: {e}")
            continue
        if opcao.maquina_id not in livres:
            motivos.append(
                f"OS {opcao.os_id}: máquina {opcao.maquina_id} ocupada, inexistente ou de outro tipo"
                " nesse horário"
            )
    return Veredito(aprovado=not motivos, motivos=motivos)
