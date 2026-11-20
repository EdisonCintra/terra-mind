"""Para quem vai cada mensagem (F2, contrato 20): o gerente da fazenda do incidente.

Antes, o Telegram mandava tudo para um `GERENTE_CHAT_ID` fixo do `.env`. Agora o destino sai do banco:
incidente → talhão → fazenda → gerentes ativos com chat (`erp.gerentes`). O `GERENTE_CHAT_ID` continua
valendo só como gerente padrão, quando a fazenda ainda não tem nenhum gerente com chat cadastrado
(instalação de uma fazenda só, ou ambiente de teste).

Funções síncronas (psycopg): os canais assíncronos as chamam com `asyncio.to_thread`.
"""

import re

from erp_mcp.db import conectar

# Os alertas do `agents` começam com "Incidente {id}:" (usado só quando o `incidente_id` não vem junto).
INCIDENTE_NO_TEXTO = re.compile(r"^\s*Incidente\s+#?(\d+)\b")


class SemDestino(Exception):
    """Não há para quem mandar: nenhum gerente com chat na fazenda e nenhum gerente padrão configurado."""


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


def chats_dos_gerentes(fazenda_id: int) -> list[int]:
    """Chats do Telegram dos gerentes ativos da fazenda, sem repetir."""
    with conectar() as conn:
        linhas = conn.execute(
            "SELECT DISTINCT telegram_chat_id FROM erp.gerentes"
            " WHERE fazenda_id = %s AND ativo AND telegram_chat_id IS NOT NULL ORDER BY telegram_chat_id",
            (fazenda_id,),
        ).fetchall()
    return [linha["telegram_chat_id"] for linha in linhas]


def todos_os_chats_de_gerentes() -> set[int]:
    """Chats de qualquer gerente ativo de qualquer fazenda (quem pode fazer pergunta livre à bula)."""
    with conectar() as conn:
        linhas = conn.execute(
            "SELECT DISTINCT telegram_chat_id FROM erp.gerentes WHERE ativo AND telegram_chat_id IS NOT NULL"
        ).fetchall()
    return {linha["telegram_chat_id"] for linha in linhas}


def destinos_do_incidente(incidente_id: int | None, padrao: int | None) -> list[int]:
    """Chats que recebem a mensagem do incidente.

    Com o incidente conhecido: os gerentes da fazenda dele; se a fazenda não tem nenhum com chat, o gerente
    padrão. Sem incidente (mensagem que não é de uma fazenda): só o padrão. Nada disso → `SemDestino`
    (uma aprovação perdida não pode sumir em silêncio).
    """
    chats: list[int] = []
    if incidente_id is not None:
        fazenda = fazenda_do_incidente(incidente_id)
        if fazenda is not None:
            chats = chats_dos_gerentes(fazenda)
    if not chats and padrao is not None:
        chats = [padrao]
    if not chats:
        raise SemDestino(
            f"nenhum gerente com chat para o incidente {incidente_id}: cadastre o chat do gerente da "
            "fazenda em erp.gerentes ou defina GERENTE_CHAT_ID (gerente padrão)"
        )
    return chats
