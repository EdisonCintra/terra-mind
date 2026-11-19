"""Escritas do painel no mini-ERP: criar OS (G3) e concluir OS (contrato 28, gatilho do G7).

Mesmas regras das escritas do MCP: a condição é checada no próprio SQL, e toda escrita grava
`agente.auditoria` com o nó `painel`, na mesma transação. Devolvem `{"ok": true, ...}` ou
`{"ok": false, "erro": "<mensagem para a tela>"}`.

São as telas mínimas para a agenda e o relatório funcionarem de ponta a ponta. O cadastro completo
(F1) e a conclusão pelo operador no celular (F6) são da frente do Felipe e podem substituí-las.
"""

from datetime import datetime, timedelta
from typing import Any

from erp_mcp.db import conectar, para_json, registrar_auditoria

TIPO_MAQUINA = "pulverizador"
MODALIDADES = ("terrestre", "drone", "aerea")


def criar_os(
    talhao_id: int,
    insumo_id: int,
    dose_por_ha: float,
    inicio: datetime,
    duracao_horas: float,
    maquina_id: int,
    operador_id: int | None,
    por: str = "painel",
) -> dict[str, Any]:
    """Cria uma OS de pulverização `agendada`. Só aceita máquina do tipo certo e livre no horário (a
    mesma regra de `disponibilidade_maquinas`) e insumo com estoque para a área do talhão."""
    fim = inicio + timedelta(hours=duracao_horas)
    with conectar() as conn:
        talhao = conn.execute(
            "SELECT id, nome, area_ha FROM erp.talhoes WHERE id = %s", (talhao_id,)
        ).fetchone()
        insumo = conn.execute(
            "SELECT id, produto, estoque, unidade FROM erp.insumos WHERE id = %s", (insumo_id,)
        ).fetchone()
        if talhao is None or insumo is None:
            return {"ok": False, "erro": "Talhão ou produto não encontrado."}
        necessario = float(talhao["area_ha"]) * dose_por_ha
        if float(insumo["estoque"]) < necessario:
            return {
                "ok": False,
                "erro": f"Estoque de {insumo['produto']} insuficiente: a OS pede {necessario:.1f}"
                f" {insumo['unidade']} e há {float(insumo['estoque']):.1f}.",
            }
        # A linha da máquina fica travada até o commit: duas OS criadas juntas não pegam a mesma.
        maquina = conn.execute(
            "SELECT id, nome, tipo, status FROM erp.maquinas WHERE id = %s FOR UPDATE", (maquina_id,)
        ).fetchone()
        if maquina is None or maquina["tipo"] != TIPO_MAQUINA or maquina["status"] != "disponivel":
            return {"ok": False, "erro": "Escolha um pulverizador disponível."}
        ocupada = conn.execute(
            """
            SELECT id FROM erp.ordens_servico
            WHERE maquina_id = %s AND status IN ('agendada', 'reagendada')
              AND inicio_previsto < %s AND %s < inicio_previsto + duracao_horas * interval '1 hour'
            LIMIT 1
            """,
            (maquina_id, fim, inicio),
        ).fetchone()
        if ocupada:
            return {"ok": False, "erro": f"{maquina['nome']} já está na OS {ocupada['id']} nesse horário."}
        nova = conn.execute(
            """
            INSERT INTO erp.ordens_servico
                (talhao_id, tipo_operacao, insumo_id, dose_por_ha, inicio_previsto, duracao_horas,
                 maquina_id, operador_id, status)
            VALUES (%s, 'pulverizacao', %s, %s, %s, %s, %s, %s, 'agendada') RETURNING *
            """,
            (talhao_id, insumo_id, dose_por_ha, inicio, duracao_horas, maquina_id, operador_id),
        ).fetchone()
        registrar_auditoria(conn, None, "painel", "criar_os", None, nova, f"OS criada por {por}")
        return {"ok": True, "os": para_json(nova)}


def concluir_os(os_id: int, execucao: dict[str, Any], por: str = "painel") -> dict[str, Any]:
    """Marca a OS como `executada` e grava os dados reais em `erp.execucoes_os` (contrato 28).

    Só conclui OS `agendada` ou `reagendada`: OS suspensa precisa primeiro de um plano aprovado.
    """
    if execucao.get("modalidade") not in MODALIDADES:
        return {"ok": False, "erro": "Modalidade inválida."}
    if execucao["fim_real"] <= execucao["inicio_real"]:
        return {"ok": False, "erro": "O término precisa ser depois do início."}
    colunas = (
        "inicio_real", "fim_real", "area_aplicada_ha", "dose_real_por_ha", "volume_calda_l_ha", "modalidade",
        "maquina_id", "operador_id", "receituario_numero", "responsavel_tecnico", "registro_profissional",
        "altura_voo_m", "largura_faixa_m", "aeronave", "ponta", "observacoes",
    )  # fmt: skip
    with conectar() as conn:
        antes = conn.execute("SELECT * FROM erp.ordens_servico WHERE id = %s", (os_id,)).fetchone()
        depois = conn.execute(
            "UPDATE erp.ordens_servico SET status = 'executada'"
            " WHERE id = %s AND status IN ('agendada', 'reagendada') RETURNING *",
            (os_id,),
        ).fetchone()
        if depois is None:
            atual = antes["status"] if antes else "inexistente"
            return {"ok": False, "erro": f"A OS {os_id} não pode ser concluída (status atual: {atual})."}
        valores = [execucao.get(c) for c in colunas]
        # Sem máquina ou operador informados, valem os da OS.
        valores[colunas.index("maquina_id")] = execucao.get("maquina_id") or antes["maquina_id"]
        valores[colunas.index("operador_id")] = execucao.get("operador_id") or antes["operador_id"]
        gravada = conn.execute(
            f"INSERT INTO erp.execucoes_os (os_id, {', '.join(colunas)}, registrado_por)"  # noqa: S608
            f" VALUES (%s, {', '.join(['%s'] * len(colunas))}, %s) RETURNING *",
            (os_id, *valores, por),
        ).fetchone()
        registrar_auditoria(
            conn,
            None,
            "painel",
            "concluir_os",
            antes,
            {**depois, "execucao": gravada},
            f"OS concluída por {por}",
        )
        return {"ok": True, "os": para_json(depois)}
