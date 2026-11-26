"""Para quem vai cada mensagem (F2, contrato 20): o gerente da fazenda do incidente.

Antes, o Telegram mandava tudo para um `GERENTE_CHAT_ID` fixo do `.env`. Agora o destino sai do banco:
incidente → talhão → fazenda → gerentes ativos com contato no canal (`erp.gerentes`). O gerente padrão do
`.env` (`GERENTE_CHAT_ID` no Telegram, `WHATSAPP_GERENTE_TELEFONE` no WhatsApp) continua valendo só quando
a fazenda ainda não tem nenhum gerente com contato cadastrado (instalação de uma fazenda só, ou teste).

O contato é uma coluna de `erp.gerentes` por canal (`CONTATO_TELEGRAM`, `CONTATO_WHATSAPP`): o canal
passa a sua. Funções síncronas (psycopg): os canais assíncronos as chamam com `asyncio.to_thread`.
"""

import re

from psycopg import sql

from erp_mcp.db import conectar

CONTATO_TELEGRAM = "telegram_chat_id"
CONTATO_WHATSAPP = "whatsapp_telefone"
_CONTATOS = (CONTATO_TELEGRAM, CONTATO_WHATSAPP)

# Os alertas do `agents` começam com "Incidente {id}:" (usado só quando o `incidente_id` não vem junto).
INCIDENTE_NO_TEXTO = re.compile(r"^\s*Incidente\s+#?(\d+)\b")


class SemDestino(Exception):
    """Não há para quem mandar: nenhum gerente com contato na fazenda e nenhum gerente padrão configurado."""


def _coluna(contato: str) -> sql.Identifier:
    if contato not in _CONTATOS:  # a coluna vem do código, nunca do usuário; a lista fecha o que é aceito
        raise ValueError(f"contato desconhecido: {contato!r}")
    return sql.Identifier(contato)


def incidente_do_texto(texto: str) -> int | None:
    m = INCIDENTE_NO_TEXTO.match(texto)
    return int(m.group(1)) if m else None


def fazenda_do_incidente(incidente_id: int) -> int | None:
    with conectar() as conn:
        linha = conn.execute(
            "SELECT t.fazenda_id FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id"
            " WHERE i.id = %s",
            (incidente_id,),
        ).fetchone()
    return linha["fazenda_id"] if linha else None


def chats_dos_gerentes(fazenda_id: int, contato: str = CONTATO_TELEGRAM) -> list[int]:
    """Contatos dos gerentes ativos da fazenda no canal, sem repetir."""
    col = _coluna(contato)
    consulta = sql.SQL(
        "SELECT DISTINCT {c} AS contato FROM erp.gerentes"
        " WHERE fazenda_id = %s AND ativo AND {c} IS NOT NULL ORDER BY {c}"
    ).format(c=col)
    with conectar() as conn:
        return [linha["contato"] for linha in conn.execute(consulta, (fazenda_id,)).fetchall()]


def todos_os_chats_de_gerentes(contato: str = CONTATO_TELEGRAM) -> set[int]:
    """Contatos de qualquer gerente ativo de qualquer fazenda (quem pode fazer pergunta livre à bula)."""
    col = _coluna(contato)
    consulta = sql.SQL(
        "SELECT DISTINCT {c} AS contato FROM erp.gerentes WHERE ativo AND {c} IS NOT NULL"
    ).format(c=col)
    with conectar() as conn:
        return {linha["contato"] for linha in conn.execute(consulta).fetchall()}


def destinos_do_incidente(
    incidente_id: int | None, padrao: int | None, contato: str = CONTATO_TELEGRAM
) -> list[int]:
    """Contatos que recebem a mensagem do incidente.

    Com o incidente conhecido: os gerentes da fazenda dele; se a fazenda não tem nenhum com contato, o gerente
    padrão. Sem incidente (mensagem que não é de uma fazenda): só o padrão. Nada disso → `SemDestino`
    (uma aprovação perdida não pode sumir em silêncio).
    """
    destinos: list[int] = []
    if incidente_id is not None:
        fazenda = fazenda_do_incidente(incidente_id)
        if fazenda is not None:
            destinos = chats_dos_gerentes(fazenda, contato)
    if not destinos and padrao is not None:
        destinos = [padrao]
    if not destinos:
        raise SemDestino(
            f"nenhum gerente com contato para o incidente {incidente_id}: cadastre o contato do gerente da "
            "fazenda em erp.gerentes ou defina o gerente padrão no .env"
        )
    return destinos


def pode_aprovar(
    incidente_id: int, remetente: int, padrao: int | None, contato: str = CONTATO_TELEGRAM
) -> bool:
    """Só um gerente da fazenda do incidente (ou o gerente padrão, se a fazenda não tem nenhum) aprova."""
    try:
        return remetente in destinos_do_incidente(incidente_id, padrao, contato)
    except SemDestino:
        return False
