"""Criação de OS de pulverização: as regras e a escrita, compartilhadas pelo painel e pelo chat.

O painel (`painel/escrita.py`) chama `criar_os` direto; o chat chama pela ferramenta MCP `abrir_os`
(`erp_mcp/escrita.py`), depois que o gerente confirma a proposta do Agente Operacional. As regras são as
mesmas nos dois caminhos e ficam no SQL, na mesma transação da escrita e da auditoria.
"""

from datetime import datetime, timedelta
from typing import Any

from erp_mcp.db import conectar, para_json, registrar_auditoria

TIPO_MAQUINA = "pulverizador"


def validar_os(
    conn,
    talhao: dict[str, Any],
    insumo: dict[str, Any],
    dose_por_ha: float,
    inicio: datetime,
    duracao_horas: float,
    maquina_id: int,
    operador_id: int | None,
    ignorar_os_id: int | None = None,
) -> dict[str, Any] | None:
    """Regras da OS, as mesmas ao criar e ao editar. Devolve `{"ok": False, "erro": ...}` ou `None`.

    Só aceita talhão, produto, máquina e operador ativos e da mesma fazenda; pulverizador disponível e livre
    no horário (a mesma regra de `disponibilidade_maquinas`); insumo com estoque para a área do talhão.
    `ignorar_os_id` tira a própria OS da conta de conflito (edição).
    """
    fim = inicio + timedelta(hours=duracao_horas)
    if not talhao["ativo"]:
        return {"ok": False, "erro": f"O talhão {talhao['nome']} está desativado."}
    if not insumo["ativo"]:
        return {"ok": False, "erro": f"O produto {insumo['produto']} está desativado."}
    if insumo["fazenda_id"] != talhao["fazenda_id"]:
        return {"ok": False, "erro": f"{insumo['produto']} não é do estoque da fazenda deste talhão."}
    necessario = float(talhao["area_ha"]) * dose_por_ha
    if float(insumo["estoque"]) < necessario:
        return {
            "ok": False,
            "erro": f"Estoque de {insumo['produto']} insuficiente: a OS pede {necessario:.1f}"
            f" {insumo['unidade']} e há {float(insumo['estoque']):.1f}.",
        }
    # A linha da máquina fica travada até o commit: duas OS criadas juntas não pegam a mesma.
    maquina = conn.execute(
        "SELECT id, fazenda_id, nome, tipo, status, ativo FROM erp.maquinas WHERE id = %s FOR UPDATE",
        (maquina_id,),
    ).fetchone()
    if (
        maquina is None
        or not maquina["ativo"]
        or maquina["tipo"] != TIPO_MAQUINA
        or maquina["status"] != "disponivel"
        or maquina["fazenda_id"] != talhao["fazenda_id"]
    ):
        return {"ok": False, "erro": "Escolha um pulverizador disponível desta fazenda."}
    if operador_id is not None:
        operador = conn.execute(
            "SELECT fazenda_id, ativo FROM erp.operadores WHERE id = %s", (operador_id,)
        ).fetchone()
        if operador is None or not operador["ativo"] or operador["fazenda_id"] != talhao["fazenda_id"]:
            return {"ok": False, "erro": "Escolha um operador desta fazenda."}
    ocupada = conn.execute(
        """
        SELECT id FROM erp.ordens_servico
        WHERE maquina_id = %s AND status IN ('agendada', 'reagendada')
          AND (%s::int IS NULL OR id <> %s)
          AND inicio_previsto < %s AND %s < inicio_previsto + duracao_horas * interval '1 hour'
        LIMIT 1
        """,
        (maquina_id, ignorar_os_id, ignorar_os_id, fim, inicio),
    ).fetchone()
    if ocupada:
        return {"ok": False, "erro": f"{maquina['nome']} já está na OS {ocupada['id']} nesse horário."}
    return None


def criar_os(
    talhao_id: int,
    insumo_id: int,
    dose_por_ha: float,
    inicio: datetime,
    duracao_horas: float,
    maquina_id: int,
    operador_id: int | None,
    por: str = "painel",
    no: str = "painel",
) -> dict[str, Any]:
    """Cria uma OS de pulverização `agendada` (regras em `validar_os`). `no` vai para a auditoria."""
    with conectar() as conn:
        talhao = conn.execute(
            "SELECT id, fazenda_id, nome, area_ha, ativo FROM erp.talhoes WHERE id = %s", (talhao_id,)
        ).fetchone()
        insumo = conn.execute(
            "SELECT id, fazenda_id, produto, estoque, unidade, ativo FROM erp.insumos WHERE id = %s",
            (insumo_id,),
        ).fetchone()
        if talhao is None or insumo is None:
            return {"ok": False, "erro": "Talhão ou produto não encontrado."}
        erro = validar_os(conn, talhao, insumo, dose_por_ha, inicio, duracao_horas, maquina_id, operador_id)
        if erro:
            return erro
        nova = conn.execute(
            """
            INSERT INTO erp.ordens_servico
                (talhao_id, tipo_operacao, insumo_id, dose_por_ha, inicio_previsto, duracao_horas,
                 maquina_id, operador_id, status)
            VALUES (%s, 'pulverizacao', %s, %s, %s, %s, %s, %s, 'agendada') RETURNING *
            """,
            (talhao_id, insumo_id, dose_por_ha, inicio, duracao_horas, maquina_id, operador_id),
        ).fetchone()
        registrar_auditoria(conn, None, no, "criar_os", None, nova, f"OS criada por {por}")
        return {"ok": True, "os": para_json(nova)}
