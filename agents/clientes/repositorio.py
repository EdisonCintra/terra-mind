"""Leitura e escrita no schema `agente` (contratos 4 e 8). SQL explícito, sem ORM."""

import json
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agents.modelos import Gatilho, HoraPrevisao, Incidente, LimiteProduto

ResultadoResposta = Literal["ok", "repetida", "desconhecida"]


def url_asyncpg(url: str) -> str:
    """`postgresql://...` (formato do `.env`) → `postgresql+asyncpg://...`."""
    for prefixo in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgres://", "postgresql://"):
        if url.startswith(prefixo):
            return "postgresql+asyncpg://" + url[len(prefixo) :]
    return url


def _json(valor: Any) -> str | None:
    return None if valor is None else json.dumps(valor, default=str, ensure_ascii=False)


def _carregar(valor: Any) -> Any:
    return json.loads(valor) if isinstance(valor, str) else valor


def _como_lista(valor: Any) -> list:
    valor = _carregar(valor)
    if valor is None:
        return []
    return valor if isinstance(valor, list) else [valor]


class RepositorioAgente(Protocol):
    async def carregar_incidente(self, incidente_id: int) -> Incidente | None: ...
    async def mudar_status(self, incidente_id: int, para: str, de: set[str] | None = None) -> bool: ...
    async def criar_plano(self, incidente_id: int, opcoes_json: dict) -> int: ...
    async def registrar_resposta(
        self, plano_id: int, incidente_id: int, resposta: str, respondido_por: str
    ) -> ResultadoResposta: ...
    async def plano_aprovado(self, plano_id: int) -> bool: ...
    async def planos_para_escalar(self, agora: datetime, timeout_min: int) -> list[dict]:
        """`[{plano_id, incidente_id, criado_em, opcoes_json}]` dos planos sem resposta nem escalonamento."""
        ...

    async def marcar_escalado(self, plano_id: int, agora: datetime) -> None: ...
    async def auditar(
        self,
        incidente_id: int | None,
        no: str,
        acao: str,
        antes: Any = None,
        depois: Any = None,
        justificativa: str | None = None,
    ) -> None: ...

    async def gravar_impactos(self, incidente_id: int, impactos: list[dict]) -> None:
        """Contrato 25: uma linha por OS suspensa, com `os_id`, `talhao_id`, `produto`, `condicao`,
        `valor_aplicacao_rs`, `fator`, `fonte` e `perda_rs`. Regravar o mesmo incidente atualiza."""
        ...

    async def situar_impactos(
        self, incidente_id: int, situacao: str, de: set[str] | None = None, plano_id: int | None = None
    ) -> None: ...
    async def limites_do_produto(self, produto: str) -> list[LimiteProduto]:
        """Contrato 26: os limites lidos da bula do produto (lista vazia = bula ainda não lida)."""
        ...

    async def salvar_limites(self, produto: str, limites: list[LimiteProduto]) -> None: ...
    async def proxima_versao_relatorio(self, os_id: int) -> int: ...
    async def salvar_relatorio(
        self, os_id: int, versao: int, dados: dict, sha256: str, pdf: bytes, conforme: bool | None
    ) -> dict:
        """Guarda uma versão do relatório de aplicação e devolve `{id, os_id, versao, gerado_em}`.
        Duas gerações simultâneas da mesma OS disputam a mesma versão: a segunda falha no UNIQUE."""
        ...


class RepositorioPostgres:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    @classmethod
    def de_url(cls, database_url: str) -> "RepositorioPostgres":
        return cls(create_async_engine(url_asyncpg(database_url), pool_pre_ping=True))

    async def carregar_incidente(self, incidente_id):
        async with self.engine.connect() as conn:
            linha = (
                (
                    await conn.execute(
                        text(
                            "SELECT id, talhao_id, status, gatilho, previsao_snapshot, criado_em"
                            " FROM agente.incidentes WHERE id = :id"
                        ),
                        {"id": incidente_id},
                    )
                )
                .mappings()
                .first()
            )
        if linha is None:
            return None
        return Incidente(
            id=linha["id"],
            talhao_id=linha["talhao_id"],
            status=linha["status"],
            gatilhos=[Gatilho.model_validate(g) for g in _como_lista(linha["gatilho"])],
            horas=[HoraPrevisao.model_validate(h) for h in _como_lista(linha["previsao_snapshot"])],
            criado_em=linha["criado_em"],
        )

    async def mudar_status(self, incidente_id, para, de=None):
        sql = "UPDATE agente.incidentes SET status = :para WHERE id = :id"
        params: dict[str, Any] = {"para": para, "id": incidente_id}
        if de is not None:
            sql += " AND status = ANY(:de)"
            params["de"] = list(de)
        async with self.engine.begin() as conn:
            return (await conn.execute(text(sql + " RETURNING id"), params)).first() is not None

    async def criar_plano(self, incidente_id, opcoes_json):
        async with self.engine.begin() as conn:
            return (
                await conn.execute(
                    text(
                        "INSERT INTO agente.planos (incidente_id, opcoes_json)"
                        " VALUES (:inc, CAST(:opcoes AS jsonb)) RETURNING id"
                    ),
                    {"inc": incidente_id, "opcoes": _json(opcoes_json)},
                )
            ).scalar_one()

    async def registrar_resposta(self, plano_id, incidente_id, resposta, respondido_por):
        async with self.engine.begin() as conn:
            gravou = (
                await conn.execute(
                    text(
                        "UPDATE agente.planos SET respondido_em = now(),"
                        " aprovado_por = CASE WHEN :resp = 'SIM' THEN :quem ELSE NULL END,"
                        " opcao_escolhida = CASE WHEN :resp = 'SIM' THEN 0 ELSE NULL END"
                        " WHERE id = :id AND incidente_id = :inc AND respondido_em IS NULL RETURNING id"
                    ),
                    {"resp": resposta, "quem": respondido_por, "id": plano_id, "inc": incidente_id},
                )
            ).first()
            if gravou:
                return "ok"
            existe = (
                await conn.execute(
                    text("SELECT 1 FROM agente.planos WHERE id = :id AND incidente_id = :inc"),
                    {"id": plano_id, "inc": incidente_id},
                )
            ).first()
        return "repetida" if existe else "desconhecida"

    async def plano_aprovado(self, plano_id):
        async with self.engine.connect() as conn:
            linha = (
                await conn.execute(
                    text("SELECT aprovado_por IS NOT NULL FROM agente.planos WHERE id = :id"),
                    {"id": plano_id},
                )
            ).first()
        return bool(linha and linha[0])

    async def planos_para_escalar(self, agora, timeout_min):
        async with self.engine.connect() as conn:
            linhas = (
                (
                    await conn.execute(
                        text(
                            "SELECT id AS plano_id, incidente_id, criado_em, opcoes_json FROM agente.planos"
                            " WHERE respondido_em IS NULL AND escalado_em IS NULL AND criado_em <= :limite"
                            " ORDER BY id"
                        ),
                        {"limite": agora - timedelta(minutes=timeout_min)},
                    )
                )
                .mappings()
                .all()
            )
        return [dict(linha) | {"opcoes_json": _carregar(linha["opcoes_json"])} for linha in linhas]

    async def marcar_escalado(self, plano_id, agora):
        async with self.engine.begin() as conn:
            await conn.execute(
                text("UPDATE agente.planos SET escalado_em = :agora WHERE id = :id"),
                {"agora": agora, "id": plano_id},
            )

    async def auditar(self, incidente_id, no, acao, antes=None, depois=None, justificativa=None):
        async with self.engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO agente.auditoria (incidente_id, no, acao, antes, depois, justificativa)"
                    " VALUES (:inc, :no, :acao, CAST(:antes AS jsonb), CAST(:depois AS jsonb), :just)"
                ),
                {
                    "inc": incidente_id,
                    "no": no,
                    "acao": acao,
                    "antes": _json(antes),
                    "depois": _json(depois),
                    "just": justificativa,
                },
            )

    async def gravar_impactos(self, incidente_id, impactos):
        sql = text(
            "INSERT INTO agente.impactos (incidente_id, os_id, talhao_id, produto, condicao,"
            " valor_aplicacao_rs, fator, fonte, perda_rs)"
            " VALUES (:inc, :os_id, :talhao_id, :produto, :condicao, :valor_aplicacao_rs, :fator, :fonte,"
            " :perda_rs)"
            " ON CONFLICT (incidente_id, os_id) DO UPDATE SET condicao = EXCLUDED.condicao,"
            " valor_aplicacao_rs = EXCLUDED.valor_aplicacao_rs, fator = EXCLUDED.fator,"
            " fonte = EXCLUDED.fonte, perda_rs = EXCLUDED.perda_rs, atualizado_em = now()"
        )
        async with self.engine.begin() as conn:
            for impacto in impactos:
                await conn.execute(sql, {"inc": incidente_id, **impacto})

    async def situar_impactos(self, incidente_id, situacao, de=None, plano_id=None):
        sql = (
            "UPDATE agente.impactos SET situacao = :situacao, atualizado_em = now(),"
            " plano_id = COALESCE(:plano, plano_id) WHERE incidente_id = :inc"
        )
        params: dict[str, Any] = {"situacao": situacao, "plano": plano_id, "inc": incidente_id}
        if de is not None:
            sql += " AND situacao = ANY(:de)"
            params["de"] = list(de)
        async with self.engine.begin() as conn:
            await conn.execute(text(sql), params)

    async def limites_do_produto(self, produto):
        async with self.engine.connect() as conn:
            linhas = (
                (
                    await conn.execute(
                        text(
                            "SELECT parametro, sentido, limite, unidade, janela_h, trecho,"
                            " documento AS fonte_doc, pagina FROM conhecimento.limites_produto"
                            " WHERE produto = :produto ORDER BY parametro, sentido"
                        ),
                        {"produto": produto},
                    )
                )
                .mappings()
                .all()
            )
        return [LimiteProduto.model_validate(dict(linha)) for linha in linhas]

    async def salvar_limites(self, produto, limites):
        """Troca os limites do produto pelos recém-lidos, numa transação (reler a bula não duplica)."""
        async with self.engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM conhecimento.limites_produto WHERE produto = :produto"),
                {"produto": produto},
            )
            for limite in limites:
                await conn.execute(
                    text(
                        "INSERT INTO conhecimento.limites_produto (produto, parametro, sentido, limite,"
                        " unidade, janela_h, trecho, documento, pagina) VALUES (:produto, :parametro,"
                        " :sentido, :limite, :unidade, :janela_h, :trecho, :documento, :pagina)"
                    ),
                    {
                        "produto": produto,
                        "parametro": limite.parametro,
                        "sentido": limite.sentido_efetivo,
                        "limite": limite.limite,
                        "unidade": limite.unidade,
                        "janela_h": limite.janela_h,
                        "trecho": limite.trecho,
                        "documento": limite.fonte_doc,
                        "pagina": limite.pagina,
                    },
                )

    async def proxima_versao_relatorio(self, os_id):
        async with self.engine.connect() as conn:
            return (
                await conn.execute(
                    text(
                        "SELECT COALESCE(MAX(versao), 0) + 1 FROM agente.relatorios_aplicacao"
                        " WHERE os_id = :os"
                    ),
                    {"os": os_id},
                )
            ).scalar_one()

    async def salvar_relatorio(self, os_id, versao, dados, sha256, pdf, conforme):
        async with self.engine.begin() as conn:
            linha = (
                (
                    await conn.execute(
                        text(
                            "INSERT INTO agente.relatorios_aplicacao"
                            " (os_id, versao, conforme, dados, sha256, pdf)"
                            " VALUES (:os, :versao, :conforme, CAST(:dados AS jsonb), :sha, :pdf)"
                            " RETURNING id, os_id, versao, gerado_em"
                        ),
                        {
                            "os": os_id,
                            "versao": versao,
                            "conforme": conforme,
                            "dados": _json(dados),
                            "sha": sha256,
                            "pdf": pdf,
                        },
                    )
                )
                .mappings()
                .one()
            )
        return dict(linha)
