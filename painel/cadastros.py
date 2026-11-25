"""Cadastro pelo painel (F1): criar, editar e desativar talhões, máquinas, operadores, insumos e o
planejamento de safra. A OS tem as suas regras em `painel/escrita.py`.

Cada escrita valida no servidor (o navegador não é confiável), grava `agente.auditoria` com o nó `painel` na
MESMA transação e nunca apaga: desativar esconde o cadastro das listas, do Monitor e das OS novas, mas o
histórico (OS, incidentes e relatórios) continua apontando para ele. Desativar é recusado enquanto houver OS
agendada, ou incidente aberto, que dependa do cadastro: sem isso o talhão deixaria de ser vigiado com OS
dentro.

O que a tela precisa para desenhar o formulário (campos, rótulos, opções) está nas `Entidade`s abaixo.
"""

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from erp_mcp.db import conectar, registrar_auditoria
from painel import geo

CULTURAS = (("milho", "Milho"), ("soja", "Soja"))
TIPOS_INSUMO = (
    ("fungicida", "Fungicida"),
    ("herbicida", "Herbicida"),
    ("inseticida", "Inseticida"),
)  # D2/contrato 15
TIPOS_MAQUINA = (
    ("pulverizador", "Pulverizador"),
    ("trator", "Trator"),
    ("colheitadeira", "Colheitadeira"),
    ("plantadeira", "Plantadeira"),
)
SAFRA = re.compile(r"^\d{4}/\d{2}$")  # ex.: 2027/28


@dataclass(frozen=True)
class Campo:
    nome: str
    rotulo: str
    tipo: str = "text"  # text | number | integer | date | select
    obrigatorio: bool = True
    opcoes: tuple[tuple[str, str], ...] = ()
    minimo: float | None = None
    passo: str | None = None
    ajuda: str | None = None
    maximo_texto: int = 120
    padrao: str = ""


@dataclass(frozen=True)
class Entidade:
    slug: str
    titulo: str  # plural, para a aba
    singular: str
    tabela: str
    campos: tuple[Campo, ...]
    colunas: tuple[tuple[str, str], ...]  # (rótulo da lista, chave da linha)
    listar: str  # SQL com %(faz)s; devolve as linhas com `id` e `ativo`
    coluna_desativa: str = "ativo"  # "ativo" (booleano) ou "status" (planejamento: cancelado)
    tem_fazenda: bool = True


MAQUINAS = Entidade(
    "maquinas",
    "Máquinas",
    "Máquina",
    "maquinas",
    (
        Campo("nome", "Nome"),
        Campo("tipo", "Tipo", "select", opcoes=TIPOS_MAQUINA, ajuda="A OS só usa pulverizador."),
        Campo(
            "status",
            "Situação",
            "select",
            opcoes=(("disponivel", "Disponível"), ("manutencao", "Em manutenção")),
        ),
    ),
    (("Nome", "nome"), ("Tipo", "tipo"), ("Situação", "status")),
    "SELECT * FROM erp.maquinas WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s)"
    " ORDER BY NOT ativo, nome, id",
)

OPERADORES = Entidade(
    "operadores",
    "Operadores",
    "Operador",
    "operadores",
    (
        Campo("nome", "Nome"),
        Campo(
            "telegram_chat_id",
            "Chat do Telegram",
            "integer",
            obrigatorio=False,
            ajuda="Para o aviso de OS remarcada pelo Telegram.",
        ),
        Campo(
            "whatsapp_telefone",
            "Telefone do WhatsApp",
            "integer",
            obrigatorio=False,
            ajuda="Com DDI e DDD, só dígitos (ex.: 5564999998888).",
        ),
    ),
    (("Nome", "nome"), ("Telegram", "telegram_chat_id"), ("WhatsApp", "whatsapp_telefone")),
    "SELECT * FROM erp.operadores WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s)"
    " ORDER BY NOT ativo, nome, id",
)

INSUMOS = Entidade(
    "insumos",
    "Insumos",
    "Insumo",
    "insumos",
    (
        Campo(
            "produto", "Produto", ajuda="O nome igual ao da bula (contrato 1). Use a busca para preencher."
        ),
        Campo("tipo", "Tipo", "select", opcoes=TIPOS_INSUMO),
        Campo("unidade", "Unidade", "select", opcoes=(("L", "Litro (L)"), ("kg", "Quilo (kg)"))),
        Campo("preco_unitario", "Preço por unidade (R$)", "number", minimo=0, passo="0.01"),
        Campo("estoque", "Estoque", "number", minimo=0, passo="0.01"),
        Campo(
            "bula_id",
            "Bula (id)",
            obrigatorio=False,
            ajuda="Preenchido pela busca. Sem bula, o Avaliador não cita limite.",
        ),
    ),
    (
        ("Produto", "produto"),
        ("Tipo", "tipo"),
        ("Estoque", "estoque_txt"),
        ("Preço", "preco_txt"),
        ("Bula", "bula_id"),
    ),
    "SELECT * FROM erp.insumos WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s)"
    " ORDER BY NOT ativo, produto, id",
)

SAFRA_PLANEJADA = Entidade(
    "safra",
    "Safra",
    "Planejamento de safra",
    "planejamento_safra",
    (
        Campo("talhao_id", "Talhão", "select"),  # opções dinâmicas: os talhões ativos da fazenda
        Campo("safra", "Safra", ajuda="Ano agrícola, no formato 2027/28."),
        Campo("cultura", "Cultura", "select", opcoes=CULTURAS),
        Campo("plantio_previsto", "Plantio previsto", "date"),
        Campo(
            "status",
            "Situação",
            "select",
            opcoes=(("planejado", "Planejado"), ("plantado", "Plantado"), ("cancelado", "Cancelado")),
            padrao="planejado",
        ),
        Campo("observacoes", "Observações", obrigatorio=False, maximo_texto=500),
    ),
    (
        ("Talhão", "talhao"),
        ("Safra", "safra"),
        ("Cultura", "cultura"),
        ("Plantio previsto", "plantio_txt"),
        ("Situação", "status"),
    ),
    "SELECT p.*, t.nome AS talhao, t.fazenda_id, (p.status <> 'cancelado') AS ativo"
    " FROM erp.planejamento_safra p JOIN erp.talhoes t ON t.id = p.talhao_id"
    " WHERE (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s) ORDER BY p.plantio_previsto DESC, p.id",
    coluna_desativa="status",
    tem_fazenda=False,
)

TALHOES = Entidade(
    "talhoes",
    "Talhões",
    "Talhão",
    "talhoes",
    (
        Campo("nome", "Nome"),
        Campo("cultura", "Cultura plantada", "select", opcoes=CULTURAS),
        Campo(
            "area_ha",
            "Área (ha)",
            "number",
            obrigatorio=False,
            minimo=0.01,
            passo="0.01",
            ajuda="Em branco, usa a área do polígono desenhado.",
        ),
        Campo("data_plantio", "Data do plantio", "date"),
        Campo("estagio_fenologico", "Estágio fenológico", ajuda="Ex.: V6, R1."),
        Campo("tipo_solo", "Tipo de solo", obrigatorio=False),
    ),
    (
        ("Nome", "nome"),
        ("Cultura", "cultura"),
        ("Área", "area_txt"),
        ("Plantio", "plantio_txt"),
        ("Forma", "forma"),
    ),
    "SELECT * FROM erp.talhoes WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY NOT ativo, id",
)

ENTIDADES: dict[str, Entidade] = {
    e.slug: e for e in (TALHOES, MAQUINAS, OPERADORES, INSUMOS, SAFRA_PLANEJADA)
}


@dataclass
class Resultado:
    ok: bool
    id: int | None = None
    erros: dict[str, str] = field(default_factory=dict)
    erro_geral: str | None = None
    valores: dict[str, str] = field(
        default_factory=dict
    )  # o que o usuário digitou, para refazer o formulário


# --- conversão e validação -------------------------------------------------------------------
def _converter(campo: Campo, texto: str | None) -> Any:
    """O valor do campo ou `ValueError` com a mensagem para a tela. Campo opcional vazio vira `None`."""
    texto = (texto or "").strip()
    if not texto:
        if campo.obrigatorio:
            raise ValueError("Obrigatório.")
        return None
    if campo.tipo == "select":
        validos = {v for v, _ in campo.opcoes}
        if campo.opcoes and texto not in validos:
            raise ValueError("Escolha uma das opções.")
        if campo.nome.endswith("_id"):  # as opções vêm do banco (o servidor confere o dono depois)
            if not texto.isdecimal() or len(texto) > 9:
                raise ValueError("Escolha uma das opções.")
            return int(texto)
        return texto
    if campo.tipo == "number":
        try:
            valor = float(texto.replace(",", "."))
        except ValueError:
            raise ValueError("Use um número.") from None
        if not math.isfinite(valor):
            raise ValueError("Use um número.")
        if campo.minimo is not None and valor < campo.minimo:
            raise ValueError(f"Mínimo {campo.minimo:g}.")
        return round(valor, 2)
    if campo.tipo == "integer":
        if not re.fullmatch(r"-?\d{1,18}", texto):
            raise ValueError("Use só dígitos.")
        return int(texto)
    if campo.tipo == "date":
        try:
            return date.fromisoformat(texto)
        except ValueError:
            raise ValueError("Data inválida.") from None
    if len(texto) > campo.maximo_texto:
        raise ValueError(f"No máximo {campo.maximo_texto} caracteres.")
    return texto


def _validar_campos(ent: Entidade, form: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    valores: dict[str, Any] = {}
    erros: dict[str, str] = {}
    for campo in ent.campos:
        try:
            valores[campo.nome] = _converter(campo, form.get(campo.nome))
        except ValueError as e:
            erros[campo.nome] = str(e)
    return valores, erros


def _digitado(ent: Entidade, form: Mapping[str, str]) -> dict[str, str]:
    d = {c.nome: (form.get(c.nome) or "").strip() for c in ent.campos}
    if ent is TALHOES:
        d["geom"] = (form.get("geom") or "").strip()
    return d


def _nao_nulo_ou_padrao(ent: Entidade, valores: dict[str, Any]) -> dict[str, Any]:
    for campo in ent.campos:
        if valores.get(campo.nome) is None and campo.padrao:
            valores[campo.nome] = campo.padrao
    return valores


# --- regras por entidade ---------------------------------------------------------------------
def _regras_talhao(form: Mapping[str, str], valores: dict[str, Any], erros: dict[str, str]) -> None:
    """Geometria (contrato 18): vira `geom` e o centro (`lat`, `lon`); a área do polígono sugere `area_ha`."""
    try:
        geom = geo.validar(json.loads(form.get("geom") or "null"))
    except (ValueError, TypeError) as e:
        erros["geom"] = str(e) if isinstance(e, geo.GeometriaInvalida) else "Desenhe o talhão no mapa."
        return
    lat, lon = geo.centro(geom)
    valores["geom"] = geom
    valores["lat"], valores["lon"] = round(lat, 6), round(lon, 6)
    if valores.get("area_ha") is None and "area_ha" not in erros:
        calculada = round(geo.area_ha(geom), 2)
        if calculada > 0:
            valores["area_ha"] = calculada
        else:
            erros["area_ha"] = "Informe a área: um ponto não tem área."


def _talhao_da_fazenda(conn, talhao_id: int, fazenda_id: int) -> bool:
    linha = conn.execute("SELECT fazenda_id FROM erp.talhoes WHERE id = %s", (talhao_id,)).fetchone()
    return linha is not None and linha["fazenda_id"] == fazenda_id


def _bula_existe(conn, bula_id: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM conhecimento.documentos WHERE id = %s", (bula_id,)).fetchone() is not None
    )


def _bloqueio_ao_desativar(conn, ent: Entidade, linha: dict[str, Any]) -> str | None:
    """Mensagem se desativar deixaria OS ou incidente sem o cadastro de que dependem; `None` se pode."""
    ativa = "status IN ('agendada', 'reagendada', 'suspensa_preventiva')"
    if ent is TALHOES:
        n = conn.execute(
            f"SELECT count(*) AS n FROM erp.ordens_servico WHERE talhao_id = %s AND {ativa}", (linha["id"],)
        ).fetchone()["n"]
        if n:
            return f"O talhão tem {n} OS agendada(s) ou suspensa(s): cancele ou conclua antes de desativar."
        aberto = conn.execute(
            "SELECT id FROM agente.incidentes WHERE talhao_id = %s AND status <> 'encerrado'", (linha["id"],)
        ).fetchone()
        if aberto:
            return f"O talhão tem o incidente {aberto['id']} aberto: espere a decisão antes de desativar."
    coluna = {"maquinas": "maquina_id", "operadores": "operador_id", "insumos": "insumo_id"}.get(ent.slug)
    if coluna:
        n = conn.execute(
            f"SELECT count(*) AS n FROM erp.ordens_servico WHERE {coluna} = %s AND {ativa}", (linha["id"],)
        ).fetchone()["n"]
        if n:
            return f"{ent.singular} está em {n} OS agendada(s) ou suspensa(s): cancele ou troque antes."
    return None


def _checar_fazenda_e_regras(
    conn, ent: Entidade, valores: dict[str, Any], fazenda_id: int | None, erros: dict[str, str]
) -> None:
    if ent is INSUMOS and valores.get("bula_id") and not _bula_existe(conn, valores["bula_id"]):
        erros["bula_id"] = "Essa bula não está na base de conhecimento. Use a busca ou deixe em branco."
    if ent is SAFRA_PLANEJADA:
        if "talhao_id" not in erros and valores.get("talhao_id") is not None:
            talhao = conn.execute(
                "SELECT fazenda_id, ativo FROM erp.talhoes WHERE id = %s", (valores["talhao_id"],)
            ).fetchone()
            if (
                talhao is None
                or not talhao["ativo"]
                or (fazenda_id is not None and talhao["fazenda_id"] != fazenda_id)
            ):
                erros["talhao_id"] = "Escolha um talhão ativo desta fazenda."
        if "safra" not in erros and valores.get("safra") and not SAFRA.match(valores["safra"]):
            erros["safra"] = "Use o formato 2027/28."


# --- leitura ---------------------------------------------------------------------------------
def _decorar(ent: Entidade, linha: dict[str, Any]) -> dict[str, Any]:
    """Textos prontos para a lista (formato brasileiro), sem mexer no que veio do banco."""

    def num(v, casas=2):
        return (
            "—"
            if v is None
            else f"{float(v):,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        )

    linha = dict(linha)
    if ent is TALHOES:
        linha["area_txt"] = f"{num(linha['area_ha'])} ha"
        linha["plantio_txt"] = (
            linha["data_plantio"].strftime("%d/%m/%Y") if linha.get("data_plantio") else "—"
        )
        tipo = (linha.get("geom") or {}).get("type")
        linha["forma"] = {"Polygon": "Polígono", "Point": "Ponto"}.get(tipo, "Ponto")
    elif ent is INSUMOS:
        linha["estoque_txt"] = f"{num(linha['estoque'])} {linha['unidade']}"
        linha["preco_txt"] = f"R$ {num(linha['preco_unitario'])}"
        linha["bula_id"] = linha.get("bula_id") or "—"
    elif ent is SAFRA_PLANEJADA:
        linha["plantio_txt"] = linha["plantio_previsto"].strftime("%d/%m/%Y")
    elif ent is OPERADORES:
        linha["telegram_chat_id"] = linha["telegram_chat_id"] if linha.get("telegram_chat_id") else "—"
        linha["whatsapp_telefone"] = linha["whatsapp_telefone"] if linha.get("whatsapp_telefone") else "—"
    return linha


def listar(ent: Entidade, fazenda_id: int | None) -> list[dict[str, Any]]:
    """Todos os cadastros da fazenda (ativos primeiro), com `ativo` para o selo e o botão."""
    with conectar() as conn:
        linhas = conn.execute(ent.listar, {"faz": fazenda_id}).fetchall()
    return [_decorar(ent, linha) for linha in linhas]


def obter(ent: Entidade, registro_id: int, fazenda_id: int | None = None) -> dict[str, Any] | None:
    """O cadastro para o formulário de edição; `fazenda_id` (a do topo) esconde o de outra fazenda."""
    for linha in listar(ent, fazenda_id):
        if linha["id"] == registro_id:
            return linha
    return None


def valores_do_formulario(ent: Entidade, linha: dict[str, Any]) -> dict[str, str]:
    """O cadastro como texto, para preencher o formulário de edição."""
    d: dict[str, str] = {}
    for campo in ent.campos:
        v = linha.get(campo.nome)
        d[campo.nome] = "" if v is None else (v.isoformat() if isinstance(v, date) else str(v))
    if ent is TALHOES:
        d["geom"] = json.dumps(linha["geom"]) if linha.get("geom") else ""
    return d


# --- escrita ---------------------------------------------------------------------------------
def salvar(
    ent: Entidade,
    form: Mapping[str, str],
    fazenda_id: int | None,
    registro_id: int | None = None,
    por: str = "painel",
) -> Resultado:
    """Cria (`registro_id` None) ou edita um cadastro. `fazenda_id` é a fazenda do topo: para criar é
    obrigatória; para editar, o cadastro tem de ser dela."""
    digitado = _digitado(ent, form)
    valores, erros = _validar_campos(ent, form)
    if ent is TALHOES:
        _regras_talhao(form, valores, erros)
    if registro_id is None and ent.tem_fazenda and fazenda_id is None:
        return Resultado(
            False, erro_geral="Escolha uma fazenda no seletor do topo antes de cadastrar.", valores=digitado
        )
    try:
        with conectar() as conn:
            antes = None
            if registro_id is not None:
                antes = conn.execute(
                    sql.SQL("SELECT * FROM erp.{} WHERE id = %s FOR UPDATE").format(
                        sql.Identifier(ent.tabela)
                    ),
                    (registro_id,),
                ).fetchone()
                if antes is None or (
                    ent.tem_fazenda and fazenda_id is not None and antes["fazenda_id"] != fazenda_id
                ):
                    return Resultado(False, erro_geral=f"{ent.singular} não encontrado.", valores=digitado)
                if (
                    ent is SAFRA_PLANEJADA
                    and fazenda_id is not None
                    and not _talhao_da_fazenda(conn, antes["talhao_id"], fazenda_id)
                ):
                    return Resultado(False, erro_geral=f"{ent.singular} não encontrado.", valores=digitado)
            fazenda_do_registro = antes["fazenda_id"] if antes and ent.tem_fazenda else fazenda_id
            _checar_fazenda_e_regras(conn, ent, valores, fazenda_do_registro, erros)
            if erros:
                return Resultado(False, erros=erros, valores=digitado)
            valores = _nao_nulo_ou_padrao(ent, valores)
            colunas = [c.nome for c in ent.campos]
            if ent is TALHOES:
                colunas += ["lat", "lon", "geom"]
            dados = {c: (Jsonb(valores[c]) if c == "geom" else valores[c]) for c in colunas}
            if registro_id is None:
                if ent.tem_fazenda:
                    colunas, dados["fazenda_id"] = ["fazenda_id", *colunas], fazenda_id
                consulta = sql.SQL("INSERT INTO erp.{} ({}) VALUES ({}) RETURNING *").format(
                    sql.Identifier(ent.tabela),
                    sql.SQL(", ").join(sql.Identifier(c) for c in colunas),
                    sql.SQL(", ").join(sql.Placeholder(c) for c in colunas),
                )
                depois = conn.execute(consulta, dados).fetchone()
                acao, verbo = "criar", "criado"
            else:
                consulta = sql.SQL("UPDATE erp.{} SET {} WHERE id = %(registro_id)s RETURNING *").format(
                    sql.Identifier(ent.tabela),
                    sql.SQL(", ").join(
                        sql.SQL("{} = {}").format(sql.Identifier(c), sql.Placeholder(c)) for c in colunas
                    ),
                )
                depois = conn.execute(consulta, {**dados, "registro_id": registro_id}).fetchone()
                acao, verbo = "editar", "alterado"
            registrar_auditoria(
                conn,
                None,
                "painel",
                f"cadastro_{ent.slug}_{acao}",
                antes,
                depois,
                f"{ent.singular} {verbo} por {por}",
            )
            return Resultado(True, id=depois["id"], valores=digitado)
    except psycopg.errors.UniqueViolation:
        if ent is INSUMOS:
            return Resultado(
                False, erros={"produto": "Esse produto já está cadastrado nesta fazenda."}, valores=digitado
            )
        if ent is SAFRA_PLANEJADA:
            return Resultado(
                False, erros={"safra": "Esse talhão já tem planejamento para essa safra."}, valores=digitado
            )
        return Resultado(False, erro_geral="Já existe um cadastro igual.", valores=digitado)
    except (psycopg.errors.CheckViolation, psycopg.errors.DataError):
        return Resultado(False, erro_geral="O banco recusou os dados: confira os valores.", valores=digitado)


def alternar(
    ent: Entidade, registro_id: int, ativo: bool, fazenda_id: int | None, por: str = "painel"
) -> Resultado:
    """Desativa ou reativa. Desativar é recusado se há OS agendada ou incidente aberto que dependa dele."""
    with conectar() as conn:
        antes = conn.execute(
            sql.SQL("SELECT * FROM erp.{} WHERE id = %s FOR UPDATE").format(sql.Identifier(ent.tabela)),
            (registro_id,),
        ).fetchone()
        if antes is None:
            return Resultado(False, erro_geral=f"{ent.singular} não encontrado.")
        if ent.tem_fazenda and fazenda_id is not None and antes["fazenda_id"] != fazenda_id:
            return Resultado(False, erro_geral=f"{ent.singular} não encontrado.")
        if (
            ent is SAFRA_PLANEJADA
            and fazenda_id is not None
            and not _talhao_da_fazenda(conn, antes["talhao_id"], fazenda_id)
        ):
            return Resultado(False, erro_geral=f"{ent.singular} não encontrado.")
        if not ativo:
            motivo = _bloqueio_ao_desativar(conn, ent, antes)
            if motivo:
                return Resultado(False, erro_geral=motivo)
        if ent.coluna_desativa == "ativo":
            consulta = sql.SQL("UPDATE erp.{} SET ativo = %s WHERE id = %s RETURNING *").format(
                sql.Identifier(ent.tabela)
            )
            depois = conn.execute(consulta, (ativo, registro_id)).fetchone()
        else:
            consulta = sql.SQL("UPDATE erp.{} SET status = %s WHERE id = %s RETURNING *").format(
                sql.Identifier(ent.tabela)
            )
            depois = conn.execute(consulta, ("planejado" if ativo else "cancelado", registro_id)).fetchone()
        acao = "reativar" if ativo else "desativar"
        registrar_auditoria(
            conn,
            None,
            "painel",
            f"cadastro_{ent.slug}_{acao}",
            antes,
            depois,
            f"{ent.singular} {acao}do por {por}",
        )
        return Resultado(True, id=registro_id)


def opcoes_dinamicas(ent: Entidade, fazenda_id: int | None) -> dict[str, tuple[tuple[str, str], ...]]:
    """Opções que dependem do banco (hoje, os talhões do planejamento de safra)."""
    if ent is not SAFRA_PLANEJADA:
        return {}
    with conectar() as conn:
        talhoes = conn.execute(
            "SELECT id, nome FROM erp.talhoes"
            " WHERE ativo AND (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY id",
            {"faz": fazenda_id},
        ).fetchall()
    return {"talhao_id": tuple((str(t["id"]), t["nome"]) for t in talhoes)}


def ordens_da_fazenda(fazenda_id: int | None, limite: int = 100) -> list[dict[str, Any]]:
    """As OS para a aba de cadastro: as mais recentes primeiro, com talhão, produto, máquina e operador."""
    with conectar() as conn:
        linhas = conn.execute(
            """
            SELECT os.id, os.talhao_id, os.insumo_id, os.maquina_id, os.operador_id, os.status,
                   os.inicio_previsto, os.duracao_horas, os.dose_por_ha, t.nome AS talhao, i.produto,
                   i.unidade,
                   m.nome AS maquina, o.nome AS operador
            FROM erp.ordens_servico os
            JOIN erp.talhoes t ON t.id = os.talhao_id
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            LEFT JOIN erp.maquinas m ON m.id = os.maquina_id
            LEFT JOIN erp.operadores o ON o.id = os.operador_id
            WHERE (%(faz)s::int IS NULL OR os.fazenda_id = %(faz)s)
            ORDER BY os.inicio_previsto DESC, os.id DESC LIMIT %(limite)s
            """,
            {"faz": fazenda_id, "limite": limite},
        ).fetchall()
    return linhas
