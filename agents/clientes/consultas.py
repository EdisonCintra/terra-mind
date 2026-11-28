"""Leituras do painel para o assistente do chat: o que o gerente vê nas telas, em consultas só de leitura.

Toda consulta recebe o `Escopo` de quem pergunta: as fazendas de que ele é gerente (`erp.gerentes`), ou
todas (canal `terminal`, que roda na máquina de quem opera o sistema). Nenhuma consulta escreve, e nenhuma
devolve dado de fazenda fora do escopo (contrato 20).

Os valores saem prontos para o LLM: datas no fuso da fazenda ("27/11 14:00"), números como `float`.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from agents.rules.janelas import FUSO

CONTATOS = {"telegram": "telegram_chat_id", "whatsapp": "whatsapp_telefone"}


@dataclass(frozen=True)
class Escopo:
    """Fazendas que a pessoa pode consultar. `todas=True` só no canal terminal."""

    fazendas: tuple[int, ...] = ()
    todas: bool = False

    @property
    def vazio(self) -> bool:
        return not self.todas and not self.fazendas

    def params(self) -> dict:
        return {"todas": self.todas, "fz": list(self.fazendas)}


def _valor(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return f"{v.astimezone(FUSO):%d/%m/%Y %H:%M}"
    if isinstance(v, date):
        return f"{v:%d/%m/%Y}"
    return v


def _linha(m) -> dict:
    return {k: _valor(v) for k, v in dict(m).items()}


def _filtro(alias: str) -> str:
    return f"(CAST(:todas AS boolean) OR {alias}.fazenda_id = ANY(CAST(:fz AS int[])))"


class ConsultasPainel(Protocol):
    async def escopo_do_contato(self, canal: str, contato: int) -> Escopo: ...
    async def fazendas(self, escopo: Escopo) -> list[dict]: ...
    async def talhoes(self, escopo: Escopo) -> list[dict]: ...
    async def ordens(
        self, escopo: Escopo, desde: datetime, ate: datetime, status: str | None, talhao_id: int | None
    ) -> list[dict]: ...
    async def ordem(self, escopo: Escopo, os_id: int) -> dict | None: ...
    async def incidentes(self, escopo: Escopo, status: str | None, limite: int) -> list[dict]: ...
    async def incidente(self, escopo: Escopo, incidente_id: int) -> dict | None: ...
    async def planos_pendentes(self, escopo: Escopo) -> list[dict]: ...
    async def perda_evitada(self, escopo: Escopo, desde: date, incluir_simulacoes: bool) -> list[dict]: ...
    async def insumos(self, escopo: Escopo) -> list[dict]: ...
    async def maquinas(self, escopo: Escopo) -> list[dict]: ...
    async def previsao(self, escopo: Escopo, talhao_id: int) -> dict | None: ...


class ConsultasPostgres:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def _todas(self, sql: str, **params) -> list[dict]:
        async with self.engine.connect() as conn:
            return [_linha(m) for m in (await conn.execute(text(sql), params)).mappings()]

    async def _uma(self, sql: str, **params) -> dict | None:
        linhas = await self._todas(sql, **params)
        return linhas[0] if linhas else None

    async def escopo_do_contato(self, canal, contato):
        coluna = CONTATOS.get(canal)
        if coluna is None:
            return Escopo()
        linhas = await self._todas(
            f"SELECT DISTINCT fazenda_id FROM erp.gerentes WHERE ativo AND {coluna} = :c ORDER BY 1",
            c=contato,
        )
        return Escopo(tuple(linha["fazenda_id"] for linha in linhas))

    async def fazendas(self, escopo):
        return await self._todas(
            "SELECT f.id, f.nome FROM erp.fazendas f"
            " WHERE CAST(:todas AS boolean) OR f.id = ANY(CAST(:fz AS int[])) ORDER BY f.nome",
            **escopo.params(),
        )

    async def talhoes(self, escopo):
        return await self._todas(
            f"""
            SELECT t.id, t.nome, t.fazenda_id, f.nome AS fazenda, t.area_ha, t.cultura, t.estagio_fenologico,
                   t.data_plantio, t.tipo_solo
            FROM erp.talhoes t LEFT JOIN erp.fazendas f ON f.id = t.fazenda_id
            WHERE t.ativo AND {_filtro("t")}
            ORDER BY t.nome
            """,
            **escopo.params(),
        )

    _ORDENS = """
        SELECT os.id, os.status, os.tipo_operacao, os.inicio_previsto, os.duracao_horas, os.dose_por_ha,
               t.id AS talhao_id, t.nome AS talhao, t.area_ha, i.produto, i.unidade, i.tipo AS tipo_insumo,
               m.nome AS maquina, o.nome AS operador
        FROM erp.ordens_servico os
        JOIN erp.talhoes t ON t.id = os.talhao_id
        LEFT JOIN erp.insumos i ON i.id = os.insumo_id
        LEFT JOIN erp.maquinas m ON m.id = os.maquina_id
        LEFT JOIN erp.operadores o ON o.id = os.operador_id
    """

    async def ordens(self, escopo, desde, ate, status, talhao_id):
        return await self._todas(
            self._ORDENS
            + f"""
            WHERE {_filtro("os")} AND os.inicio_previsto BETWEEN :desde AND :ate
              AND (CAST(:status AS text) IS NULL OR os.status = :status)
              AND (CAST(:talhao AS int) IS NULL OR os.talhao_id = :talhao)
            ORDER BY os.inicio_previsto LIMIT 40
            """,
            desde=desde,
            ate=ate,
            status=status,
            talhao=talhao_id,
            **escopo.params(),
        )

    async def ordem(self, escopo, os_id):
        os = await self._uma(
            self._ORDENS + f" WHERE os.id = :id AND {_filtro('os')}", id=os_id, **escopo.params()
        )
        if os is None:
            return None
        os["impactos"] = await self._todas(
            "SELECT incidente_id, condicao, perda_rs, situacao, fonte FROM agente.impactos"
            " WHERE os_id = :id ORDER BY criado_em DESC LIMIT 5",
            id=os_id,
        )
        os["execucao"] = await self._uma(
            "SELECT inicio_real, fim_real, area_aplicada_ha, dose_real_por_ha, modalidade"
            " FROM erp.execucoes_os WHERE os_id = :id",
            id=os_id,
        )
        return os

    async def incidentes(self, escopo, status, limite):
        return await self._todas(
            f"""
            SELECT i.id, i.status, i.origem, i.criado_em, t.id AS talhao_id, t.nome AS talhao,
                   (SELECT string_agg(g->>'tipo', ', ') FROM jsonb_array_elements(
                        CASE WHEN jsonb_typeof(i.gatilho) = 'array' THEN i.gatilho ELSE '[]'::jsonb END
                   ) g) AS gatilhos
            FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE {_filtro("t")} AND (CAST(:status AS text) IS NULL OR i.status = :status)
            ORDER BY i.criado_em DESC LIMIT :limite
            """,
            status=status,
            limite=limite,
            **escopo.params(),
        )

    async def incidente(self, escopo, incidente_id):
        cabecalho = await self._uma(
            f"""
            SELECT i.id, i.status, i.origem, i.criado_em, i.gatilho AS gatilhos, t.id AS talhao_id,
                   t.nome AS talhao
            FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE i.id = :id AND {_filtro("t")}
            """,
            id=incidente_id,
            **escopo.params(),
        )
        if cabecalho is None:
            return None
        cabecalho["planos"] = await self._todas(
            "SELECT id, criado_em, respondido_em, aprovado_por, opcoes_json->>'mensagem' AS mensagem"
            " FROM agente.planos WHERE incidente_id = :id ORDER BY id",
            id=incidente_id,
        )
        cabecalho["impactos"] = await self._todas(
            "SELECT os_id, produto, condicao, perda_rs, situacao, fonte FROM agente.impactos"
            " WHERE incidente_id = :id ORDER BY os_id",
            id=incidente_id,
        )
        cabecalho["eventos"] = await self._todas(
            "SELECT ts, no, acao, justificativa FROM ("
            "  SELECT ts, no, acao, left(justificativa, 300) AS justificativa FROM agente.auditoria"
            "  WHERE incidente_id = :id ORDER BY ts DESC LIMIT 25"
            ") e ORDER BY ts",
            id=incidente_id,
        )
        return cabecalho

    async def planos_pendentes(self, escopo):
        return await self._todas(
            f"""
            SELECT p.id AS plano_id, p.incidente_id, t.nome AS talhao, p.criado_em,
                   p.opcoes_json->>'mensagem' AS mensagem
            FROM agente.planos p
            JOIN agente.incidentes i ON i.id = p.incidente_id
            JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE p.respondido_em IS NULL AND {_filtro("t")}
            ORDER BY p.criado_em
            """,
            **escopo.params(),
        )

    async def perda_evitada(self, escopo, desde, incluir_simulacoes):
        return await self._todas(
            f"""
            SELECT im.situacao, count(*) AS ordens, sum(im.perda_rs) AS perda_rs,
                   count(*) FILTER (WHERE im.perda_rs IS NULL) AS sem_fator
            FROM agente.impactos im
            JOIN agente.incidentes i ON i.id = im.incidente_id
            JOIN erp.talhoes t ON t.id = im.talhao_id
            WHERE {_filtro("t")} AND im.criado_em >= :desde
              AND (CAST(:simulacoes AS boolean) OR i.origem = 'live')
            GROUP BY im.situacao ORDER BY im.situacao
            """,
            desde=desde,
            simulacoes=incluir_simulacoes,
            **escopo.params(),
        )

    async def insumos(self, escopo):
        return await self._todas(
            f"""
            SELECT i.id, i.fazenda_id, f.nome AS fazenda, i.produto, i.tipo, i.unidade, i.estoque,
                   i.preco_unitario,
                   coalesce(sum(os.dose_por_ha * t.area_ha)
                            FILTER (WHERE os.status IN ('agendada', 'reagendada')), 0) AS comprometido
            FROM erp.insumos i
            LEFT JOIN erp.fazendas f ON f.id = i.fazenda_id
            LEFT JOIN erp.ordens_servico os ON os.insumo_id = i.id
            LEFT JOIN erp.talhoes t ON t.id = os.talhao_id
            WHERE i.ativo AND {_filtro("i")}
            GROUP BY i.id, f.nome ORDER BY i.produto, f.nome
            """,
            **escopo.params(),
        )

    async def maquinas(self, escopo):
        return await self._todas(
            f"""
            SELECT m.id, m.nome, f.nome AS fazenda, m.tipo, m.status,
                   (SELECT min(os.inicio_previsto) FROM erp.ordens_servico os
                    WHERE os.maquina_id = m.id AND os.status IN ('agendada', 'reagendada')
                      AND os.inicio_previsto >= now()) AS proxima_os
            FROM erp.maquinas m
            LEFT JOIN erp.fazendas f ON f.id = m.fazenda_id
            WHERE m.ativo AND {_filtro("m")}
            ORDER BY m.nome
            """,
            **escopo.params(),
        )

    async def previsao(self, escopo, talhao_id):
        async with self.engine.connect() as conn:
            linha = (
                (
                    await conn.execute(
                        text(
                            f"""
                        SELECT p.buscada_em, p.fonte, p.horas, t.nome AS talhao
                        FROM clima.previsoes p JOIN erp.talhoes t ON t.id = p.talhao_id
                        WHERE p.talhao_id = :id AND {_filtro("t")}
                        ORDER BY p.buscada_em DESC LIMIT 1
                        """
                        ),
                        {"id": talhao_id, **escopo.params()},
                    )
                )
                .mappings()
                .first()
            )
        return dict(linha) if linha else None
