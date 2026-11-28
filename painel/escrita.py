"""Escritas do painel no mini-ERP: criar OS (G3) e concluir OS (contrato 28, gatilho do G7).

Mesmas regras das escritas do MCP: a condição é checada no próprio SQL, e toda escrita grava
`agente.auditoria` com o nó `painel`, na mesma transação. Devolvem `{"ok": true, ...}` ou
`{"ok": false, "erro": "<mensagem para a tela>"}`.

O cadastro completo (F1) está em `painel/cadastros.py`; criar, editar e cancelar OS passam pelas regras
daqui. A tela do operador no celular (F6, `/campo`) usa a mesma `concluir_os`.
"""

from datetime import datetime, timedelta
from typing import Any

from erp_mcp.db import conectar, para_json, registrar_auditoria

TIPO_MAQUINA = "pulverizador"
MODALIDADES = ("terrestre", "drone", "aerea")


def _validar_os(
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
) -> dict[str, Any]:
    """Cria uma OS de pulverização `agendada` (regras em `_validar_os`)."""
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
        erro = _validar_os(conn, talhao, insumo, dose_por_ha, inicio, duracao_horas, maquina_id, operador_id)
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
        registrar_auditoria(conn, None, "painel", "criar_os", None, nova, f"OS criada por {por}")
        return {"ok": True, "os": para_json(nova)}


STATUS_EDITAVEIS = ("agendada", "reagendada")

# Incidente que ainda vai decidir a OS suspensa: enquanto houver um no talhão, só o plano mexe nela.
INCIDENTE_EM_ANDAMENTO = "status IN ('aberto', 'aguardando_aprovacao')"


def suspensa_liberada(conn, os: dict[str, Any]) -> bool:
    """OS suspensa sem incidente em andamento no talhão (opções esgotadas, decisão manual, falha ao manter
    a data): o gerente pode remarcar ou cancelar pelo painel; senão ela ficaria presa em suspensão."""
    if os["status"] != "suspensa_preventiva":
        return False
    em_andamento = conn.execute(
        f"SELECT 1 FROM agente.incidentes WHERE talhao_id = %s AND {INCIDENTE_EM_ANDAMENTO}",
        (os["talhao_id"],),
    ).fetchone()
    return em_andamento is None


def editar_os(
    os_id: int,
    dose_por_ha: float,
    inicio: datetime,
    duracao_horas: float,
    maquina_id: int,
    operador_id: int | None,
    por: str = "painel",
) -> dict[str, Any]:
    """Edita dose, horário, máquina e operador de uma OS `agendada` ou `reagendada`. O talhão e o produto
    não mudam: para trocá-los, cancele a OS e crie outra. As mesmas regras da criação.

    OS suspensa sem incidente em andamento (`suspensa_liberada`) também pode ser editada: é a remarcação
    manual, e ela volta a `agendada` no horário novo."""
    with conectar() as conn:
        antes = conn.execute("SELECT * FROM erp.ordens_servico WHERE id = %s FOR UPDATE", (os_id,)).fetchone()
        if antes is None:
            return {"ok": False, "erro": f"OS {os_id} não existe."}
        remarcar = suspensa_liberada(conn, antes)
        if antes["status"] == "suspensa_preventiva" and not remarcar:
            return {
                "ok": False,
                "erro": f"A OS {os_id} está suspensa por um incidente em andamento: responda o plano.",
            }
        if antes["status"] not in STATUS_EDITAVEIS and not remarcar:
            return {"ok": False, "erro": f"A OS {os_id} está {antes['status']} e não pode ser editada."}
        talhao = conn.execute(
            "SELECT id, fazenda_id, nome, area_ha, ativo FROM erp.talhoes WHERE id = %s",
            (antes["talhao_id"],),
        ).fetchone()
        insumo = conn.execute(
            "SELECT id, fazenda_id, produto, estoque, unidade, ativo FROM erp.insumos WHERE id = %s",
            (antes["insumo_id"],),
        ).fetchone()
        if insumo is None:
            return {"ok": False, "erro": f"A OS {os_id} não tem produto: não dá para conferir o estoque."}
        erro = _validar_os(
            conn, talhao, insumo, dose_por_ha, inicio, duracao_horas, maquina_id, operador_id, os_id
        )
        if erro:
            return erro
        depois = conn.execute(
            """
            UPDATE erp.ordens_servico
            SET dose_por_ha = %s, inicio_previsto = %s, duracao_horas = %s, maquina_id = %s, operador_id = %s,
                status = CASE WHEN status = 'suspensa_preventiva' THEN 'agendada' ELSE status END
            WHERE id = %s RETURNING *
            """,
            (dose_por_ha, inicio, duracao_horas, maquina_id, operador_id, os_id),
        ).fetchone()
        if remarcar:
            acao, texto = "remarcar_os_suspensa", f"OS suspensa remarcada por {por}"
        else:
            acao, texto = "editar_os", f"OS editada por {por}"
        registrar_auditoria(conn, None, "painel", acao, antes, depois, texto)
        return {"ok": True, "os": para_json(depois)}


def cancelar_os(os_id: int, por: str = "painel") -> dict[str, Any]:
    """Cancela uma OS `agendada` ou `reagendada` (a máquina fica livre). A OS suspensa por um incidente em
    andamento espera a decisão do plano: cancelar no meio dele deixaria o fluxo sem OS para remarcar. Sem
    incidente em andamento (`suspensa_liberada`), a OS suspensa também pode ser cancelada."""
    with conectar() as conn:
        antes = conn.execute("SELECT * FROM erp.ordens_servico WHERE id = %s FOR UPDATE", (os_id,)).fetchone()
        if antes is None:
            return {"ok": False, "erro": f"OS {os_id} não existe."}
        liberada = suspensa_liberada(conn, antes)
        if antes["status"] == "suspensa_preventiva" and not liberada:
            return {
                "ok": False,
                "erro": f"A OS {os_id} está suspensa por um incidente em andamento: responda o plano.",
            }
        if antes["status"] not in STATUS_EDITAVEIS and not liberada:
            return {"ok": False, "erro": f"A OS {os_id} está {antes['status']} e não pode ser cancelada."}
        depois = conn.execute(
            "UPDATE erp.ordens_servico SET status = 'cancelada' WHERE id = %s RETURNING *", (os_id,)
        ).fetchone()
        registrar_auditoria(conn, None, "painel", "cancelar_os", antes, depois, f"OS cancelada por {por}")
        return {"ok": True, "os": para_json(depois)}


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
        # Máquina e operador informados têm de ser da fazenda da OS (confere antes de gravar qualquer coisa).
        for campo, tabela, nome in (
            ("maquina_id", "erp.maquinas", "Máquina"),
            ("operador_id", "erp.operadores", "Operador"),
        ):
            if antes and execucao.get(campo):
                achado = conn.execute(
                    f"SELECT 1 FROM {tabela} WHERE id = %s AND fazenda_id = %s",  # noqa: S608
                    (execucao[campo], antes["fazenda_id"]),
                ).fetchone()
                if achado is None:
                    return {"ok": False, "erro": f"{nome} de outra fazenda ou inexistente."}
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
