"""Consultas e formatação do painel: só leitura do Postgres (as escritas ficam em `painel.escrita`)."""

import json
import os
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from erp_mcp import leitura
from erp_mcp.db import conectar

FUSO = ZoneInfo("America/Sao_Paulo")  # contrato 12

# O `agents` escreve a citação no motivo da suspensão: "<justificativa> (fonte: <doc>, p. <n>)".
CITACAO = re.compile(r"\(fonte:\s*(.+?),\s*p\.\s*(\d+)\)")


def listar_fazendas() -> list[dict]:
    """Fazendas para o seletor do painel (contrato 20), com o nome da organização.

    `[]` se `erp.fazendas` não existir (banco sem a migration 003): o painel abre sem seletor."""
    try:
        with conectar() as conn:
            return conn.execute(
                "SELECT f.id, f.nome, f.demo, o.nome AS organizacao FROM erp.fazendas f"
                " JOIN erp.organizacoes o ON o.id = f.organizacao_id ORDER BY f.demo, f.nome"
            ).fetchall()
    except psycopg.errors.UndefinedTable:
        return []


def listar_ordens(fazenda_id: int | None = None) -> list[dict]:
    """Todas as leituras do painel aceitam `fazenda_id` (contrato 20); sem ele, todas as fazendas."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT os.id, os.tipo_operacao, os.inicio_previsto, os.status,
                   t.nome AS talhao, i.produto, m.nome AS maquina, o.nome AS operador
            FROM erp.ordens_servico os
            JOIN erp.talhoes t ON t.id = os.talhao_id
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            LEFT JOIN erp.maquinas m ON m.id = os.maquina_id
            LEFT JOIN erp.operadores o ON o.id = os.operador_id
            WHERE (%(faz)s::int IS NULL OR os.fazenda_id = %(faz)s)
            ORDER BY os.inicio_previsto, os.id
            """,
            {"faz": fazenda_id},
        ).fetchall()


def listar_incidentes(limite: int = 8, fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT i.id, i.status, i.criado_em, i.talhao_id, i.origem, t.nome AS talhao
            FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
            ORDER BY i.id DESC LIMIT %(limite)s
            """,
            {"faz": fazenda_id, "limite": limite},
        ).fetchall()


ROTULO_CLASSE = {"correta": "Correta", "falso_alarme": "Falso alarme", "sem_dado": "Sem dado"}


def _mm(valor) -> str:
    return f"{float(valor):.1f}".replace(".", ",") + " mm" if valor is not None else "—"


def _janela(inicio: datetime | None, fim: datetime | None) -> str:
    if inicio is None or fim is None:
        return "—"
    return f"{inicio.astimezone(FUSO):%d/%m %H:%M}–{fim.astimezone(FUSO):%d/%m %H:%M}"


def resumo_historico(fazenda_id: int | None) -> dict:
    """Suspensões ao vivo conferidas contra a chuva observada (E6), mais recentes primeiro.

    Só os talhões da `fazenda_id` (contrato 20); sem ela, todas as fazendas."""
    with conectar() as conn:
        linhas = conn.execute(
            """
            SELECT c.incidente_id, c.os_id, t.nome AS talhao, c.janela_inicio, c.janela_fim,
                   c.chuva_prevista_mm, c.chuva_observada_mm, c.limite_mm, c.limite_fonte,
                   c.fonte_observado, c.classe, c.motivo
            FROM clima.conferencias c
            JOIN agente.incidentes i ON i.id = c.incidente_id
            JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
            ORDER BY c.janela_inicio DESC NULLS LAST, c.incidente_id DESC, c.os_id
            """,
            {"faz": fazenda_id},
        ).fetchall()
    corretas = sum(1 for r in linhas if r["classe"] == "correta")
    falsos = sum(1 for r in linhas if r["classe"] == "falso_alarme")
    erros = [
        abs(float(r["chuva_prevista_mm"]) - float(r["chuva_observada_mm"]))
        for r in linhas
        if r["chuva_prevista_mm"] is not None and r["chuva_observada_mm"] is not None
    ]
    itens = []
    for r in linhas:
        limite = _mm(r["limite_mm"]).replace(",0 mm", " mm") if r["limite_mm"] is not None else "—"
        if r["limite_mm"] is not None and r["limite_fonte"]:
            limite += f" ({r['limite_fonte']})"
        itens.append(
            {
                **r,
                "janela": _janela(r["janela_inicio"], r["janela_fim"]),
                "prevista": _mm(r["chuva_prevista_mm"]),
                "observada": _mm(r["chuva_observada_mm"]),
                "limite": limite,
                "rotulo_classe": ROTULO_CLASSE[r["classe"]],
                "reanalise": r["fonte_observado"] == "reanalise",
            }
        )
    return {
        "total": len(linhas),
        "corretas": corretas,
        "falsos_alarmes": falsos,
        "sem_dado": sum(1 for r in linhas if r["classe"] == "sem_dado"),
        "pct_corretas": round(100 * corretas / (corretas + falsos), 1) if corretas + falsos else None,
        "erro_medio_mm": round(sum(erros) / len(erros), 1) if erros else None,
        "itens": itens,
    }


def talhao_padrao(fazenda_id: int | None = None) -> int | None:
    """Primeiro talhão (da fazenda escolhida): o gráfico da Visão geral usa ele sem incidente selecionado."""
    with conectar() as conn:
        linha = conn.execute(
            "SELECT id FROM erp.talhoes WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY id LIMIT 1",
            {"faz": fazenda_id},
        ).fetchone()
    return linha["id"] if linha else None


def talhoes_com_geometria(fazenda_id: int | None = None) -> list[dict]:
    """Talhões do mapa: id, nome, lat/lon e `geom` (GeoJSON), da fazenda escolhida (sem ela, todas).
    Sem a coluna `geom` (contrato 18 ainda não entregue), `geom` é None."""
    with conectar() as conn:
        tem_geom = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'erp' AND table_name = 'talhoes' AND column_name = 'geom'"
        ).fetchone()
        coluna = "geom" if tem_geom else "NULL AS geom"
        linhas = conn.execute(
            f"SELECT id, nome, lat, lon, {coluna} FROM erp.talhoes"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY id",
            {"faz": fazenda_id},
        ).fetchall()
    return [{**t, "lat": float(t["lat"]), "lon": float(t["lon"])} for t in linhas]


def ordens_do_talhao(talhao_id: int, de: datetime, ate: datetime) -> list[dict]:
    """Ordens do talhão que começam entre `de` e `ate`, com o produto do insumo."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT os.id, os.tipo_operacao, os.inicio_previsto, os.status, i.produto
            FROM erp.ordens_servico os
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            WHERE os.talhao_id = %s AND os.inicio_previsto >= %s AND os.inicio_previsto <= %s
            ORDER BY os.inicio_previsto, os.id
            """,
            (talhao_id, de, ate),
        ).fetchall()


RUMOS = ("N", "NE", "L", "SE", "S", "SO", "O", "NO")


def rumo(graus: float) -> str:
    """Direção do vento (de onde sopra) em texto: 8 pontos, cada um com 45 graus de largura."""
    return RUMOS[int(((graus % 360) + 22.5) // 45) % 8]


def auditoria_do_incidente(incidente_id: int) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, ts, no, acao, antes, depois, justificativa FROM agente.auditoria"
            " WHERE incidente_id = %s ORDER BY ts, id",
            (incidente_id,),
        ).fetchall()


def planos_pendentes(fazenda_id: int | None = None) -> list[dict]:
    """Planos sem resposta (inclui os escalonados: a sessão segue pausada)."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT p.id, p.incidente_id, p.criado_em, p.escalado_em,
                   p.opcoes_json->>'mensagem' AS mensagem, t.nome AS talhao,
                   p.opcoes_json->'custos_atraso' AS custos_atraso
            FROM agente.planos p
            JOIN agente.incidentes i ON i.id = p.incidente_id
            JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE p.respondido_em IS NULL AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
            ORDER BY p.criado_em, p.id
            """,
            {"faz": fazenda_id},
        ).fetchall()


def listar_produtos(fazenda_id: int | None = None) -> list[str]:
    """Produtos cadastrados no ERP, para o Pergunte à bula (contrato 1: mesmo nome da bula)."""
    with conectar() as conn:
        return [
            linha["produto"]
            for linha in conn.execute(
                "SELECT DISTINCT produto FROM erp.insumos"
                " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY produto",
                {"faz": fazenda_id},
            )
        ]


def incidentes_decisao_manual(fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            """
            SELECT i.id, t.nome AS talhao,
                   (SELECT a.justificativa FROM agente.auditoria a
                    WHERE a.incidente_id = i.id AND a.acao = 'enviar_alerta'
                    ORDER BY a.id DESC LIMIT 1) AS alerta
            FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id
            WHERE i.status = 'aguardando_decisao_manual'
              AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
            ORDER BY i.id
            """,
            {"faz": fazenda_id},
        ).fetchall()


def separar_citacoes(texto: str | None) -> tuple[str, list[tuple[str, str]]]:
    """Tira as citações `(fonte: X, p. N)` do texto e devolve (texto limpo, [(fonte, página)])."""
    if not texto:
        return "", []
    citacoes = [(m.group(1), m.group(2)) for m in CITACAO.finditer(texto)]
    limpo = re.sub(r"\s{2,}", " ", CITACAO.sub("", texto)).strip(" ;")
    return limpo, citacoes


def reais(valor: float) -> str:
    inteiro, centavos = f"{valor:,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def _duracao(segundos: float) -> str:
    minutos = max(0, int(segundos // 60))
    if minutos < 60:
        return f"{minutos} min"
    horas = minutos // 60
    if horas < 24:
        return f"{horas} h"
    dias, resto = divmod(horas, 24)
    return f"{dias} dia{'s' if dias > 1 else ''}" + (f" e {resto} h" if resto else "")


def linhas_atraso(custos: Any, agora: datetime) -> list[str]:
    """G5: uma linha por OS com quando a janela proposta vence e quanto custa perdê-la.

    Os números vêm prontos do `agents` (`opcoes_json["custos_atraso"]`); aqui só o "vence em" é
    recalculado, a cada atualização da tela.
    """
    if isinstance(custos, str):
        custos = json.loads(custos)
    linhas = []
    for c in custos or []:
        janela = datetime.fromisoformat(c["janela_proposta"]).astimezone(FUSO)
        vence = datetime.fromisoformat(c["vence_em"])
        inicio = f"OS {c['os_id']}: janela de {janela:%d/%m %H:%M}"
        if agora >= vence:
            linhas.append(f"{inicio} já venceu; peça outra opção.")
            continue
        partes = [f"{inicio} vence em {_duracao((vence - agora).total_seconds())}"]
        if c.get("custo_perder_janela_rs") is not None:
            estimativa = " (estimativa)" if str(c.get("fonte_fator", "")).startswith("estimativa") else ""
            partes.append(f"perdê-la custa {reais(c['custo_perder_janela_rs'])}{estimativa}")
        if c.get("proxima_janela"):
            proxima = datetime.fromisoformat(c["proxima_janela"]).astimezone(FUSO)
            partes.append(f"próxima janela: {proxima:%d/%m %H:%M}")
        else:
            partes.append("sem outra janela na previsão")
        linhas.append(" · ".join(partes))
    return linhas


def _curto(valor: Any) -> str:
    texto = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False, default=str)
    return texto if len(texto) <= 60 else texto[:57] + "…"


def resumo(antes: Any, depois: Any) -> list[str]:
    """Linhas "campo: antes → depois" (só o que mudou) ou "campo: valor" quando não há `antes`."""
    if isinstance(antes, dict) and isinstance(depois, dict):
        mudancas = [k for k in depois if k != "id" and antes.get(k) != depois[k]]
        return [f"{k}: {_curto(antes.get(k))} → {_curto(depois[k])}" for k in mudancas[:4]]
    if isinstance(depois, dict):
        return [f"{k}: {_curto(v)}" for k, v in list(depois.items())[:4]]
    return []


# --- G1: perda evitada ------------------------------------------------------------------------
SITUACOES_IMPACTO = {
    "evitada": "Perda evitada",
    "suspensa": "Aguardando decisão",
    "recusada": "Proposta recusada",
    "decisao_manual": "Decisão manual",
}


def inicio_da_safra(hoje: date) -> date:
    """`SAFRA_INICIO` (AAAA-MM-DD) ou 1º de julho do ano-safra corrente. Vira configuração por fazenda
    quando o F2 criar `erp.fazendas`."""
    configurado = os.environ.get("SAFRA_INICIO")
    if configurado:
        return date.fromisoformat(configurado)
    return date(hoje.year if hoje.month >= 7 else hoje.year - 1, 7, 1)


def perda_evitada(
    inicio: date, fim: date, incluir_replay: bool = False, fazenda_id: int | None = None
) -> dict:
    """Números do dashboard, todos de `agente.impactos` e `agente.planos` (nada é somado no navegador).

    Incidente de replay fica de fora (contrato 27), a não ser no modo demonstração.
    """
    filtro = """
        FROM agente.impactos im
        JOIN agente.incidentes i ON i.id = im.incidente_id
        JOIN erp.talhoes t ON t.id = im.talhao_id
        WHERE im.criado_em >= %(inicio)s AND im.criado_em < %(fim)s::date + 1
          AND (%(replay)s OR i.origem = 'live')
          AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
    """
    planos = """
        FROM agente.planos p JOIN agente.incidentes i ON i.id = p.incidente_id
        JOIN erp.talhoes t ON t.id = i.talhao_id
        WHERE p.criado_em >= %(inicio)s AND p.criado_em < %(fim)s::date + 1
          AND (%(replay)s OR i.origem = 'live')
          AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
    """
    params = {"inicio": inicio, "fim": fim, "replay": incluir_replay, "faz": fazenda_id}
    with conectar() as conn:
        situacoes = conn.execute(
            "SELECT im.situacao, count(*) AS os, COALESCE(sum(im.perda_rs), 0) AS perda_rs,"
            " count(*) FILTER (WHERE im.perda_rs IS NULL) AS sem_fator,"
            " bool_or(im.fonte ILIKE 'estimativa%%') AS estimativa" + filtro + " GROUP BY im.situacao",
            params,
        ).fetchall()
        por_talhao = conn.execute(
            "SELECT t.nome AS nome, count(*) AS os, COALESCE(sum(im.perda_rs), 0) AS perda_rs"
            + filtro
            + " AND im.situacao = 'evitada' GROUP BY t.nome ORDER BY perda_rs DESC, nome",
            params,
        ).fetchall()
        por_produto = conn.execute(
            "SELECT COALESCE(im.produto, 'sem produto') AS nome, count(*) AS os,"
            " COALESCE(sum(im.perda_rs), 0) AS perda_rs"
            + filtro
            + " AND im.situacao = 'evitada' GROUP BY 1 ORDER BY perda_rs DESC, nome",
            params,
        ).fetchall()
        linhas = conn.execute(
            "SELECT im.incidente_id, im.os_id, t.nome AS talhao, im.produto, im.condicao, im.fator,"
            " im.fonte, im.valor_aplicacao_rs, im.perda_rs, im.situacao, im.criado_em, i.origem"
            + filtro
            + " ORDER BY im.criado_em DESC, im.id DESC LIMIT 200",
            params,
        ).fetchall()
        decisoes = conn.execute(
            "SELECT count(*) FILTER (WHERE p.aprovado_por IS NOT NULL) AS aprovados,"
            " count(*) FILTER (WHERE p.respondido_em IS NOT NULL AND p.aprovado_por IS NULL)"
            "   AS nao_aprovados,"
            " count(*) FILTER (WHERE p.respondido_em IS NULL) AS pendentes,"
            " avg(extract(epoch FROM p.respondido_em - p.criado_em))"
            "   FILTER (WHERE p.respondido_em IS NOT NULL) AS resposta_s" + planos,
            params,
        ).fetchone()
        manuais = conn.execute(
            "SELECT count(*) AS n FROM agente.incidentes i JOIN erp.talhoes t ON t.id = i.talhao_id"
            " WHERE i.status = 'aguardando_decisao_manual'"
            " AND i.criado_em >= %(inicio)s AND i.criado_em < %(fim)s::date + 1"
            " AND (%(replay)s OR i.origem = 'live')"
            " AND (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)",
            params,
        ).fetchone()
        area = conn.execute(
            "SELECT COALESCE(sum(area_ha), 0) AS ha FROM erp.talhoes"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s)",
            params,
        ).fetchone()
    por_situacao = {s["situacao"]: s for s in situacoes}
    evitada = por_situacao.get("evitada") or {"os": 0, "perda_rs": 0, "sem_fator": 0, "estimativa": False}
    total = float(evitada["perda_rs"])
    em_aberto = float((por_situacao.get("suspensa") or {}).get("perda_rs", 0))
    resultado = {
        "inicio": inicio,
        "fim": fim,
        "total_rs": total,
        "total": reais(total),
        "os_protegidas": evitada["os"],
        "sem_fator": evitada["sem_fator"],
        "estimativa": bool(evitada["estimativa"]),
        "em_aberto_rs": em_aberto,  # o tom do KPI vem do valor (painel: cor só para estado)
        "em_aberto": reais(em_aberto),
        "situacoes": [
            {
                "rotulo": rotulo,
                "os": por_situacao[s]["os"],
                "valor": reais(float(por_situacao[s]["perda_rs"])),
            }
            for s, rotulo in SITUACOES_IMPACTO.items()
            if s in por_situacao
        ],
        "por_talhao": _com_barras(por_talhao),
        "por_produto": _com_barras(por_produto),
        "planos": {
            "aprovados": decisoes["aprovados"],
            "nao_aprovados": decisoes["nao_aprovados"],
            "pendentes": decisoes["pendentes"],
            "manuais": manuais["n"],
            "tempo_resposta": None
            if decisoes["resposta_s"] is None
            else _duracao(float(decisoes["resposta_s"])),
        },
        "linhas": [
            {
                **linha,
                "perda": "sem fator" if linha["perda_rs"] is None else reais(float(linha["perda_rs"])),
                "estimativa": str(linha["fonte"]).lower().startswith("estimativa"),
                "situacao_rotulo": SITUACOES_IMPACTO.get(linha["situacao"], linha["situacao"]),
            }
            for linha in linhas
        ],
        "retorno": None,
    }
    # Retorno sobre a assinatura: só aparece com o preço por hectare configurado (PRECO_HA_SAFRA).
    preco = float(os.environ.get("PRECO_HA_SAFRA") or 0)
    if preco > 0 and float(area["ha"]) > 0:
        custo = preco * float(area["ha"])
        resultado["retorno"] = {
            "custo": reais(custo),
            "area": f"{float(area['ha']):.0f}".replace(".", ","),
            "preco": reais(preco),
            "vezes": f"{total / custo:.1f}".replace(".", ","),
        }
    return resultado


def _com_barras(linhas: list[dict]) -> list[dict]:
    """Acrescenta o valor formatado e a largura da barra (0 a 100) em relação ao maior."""
    maior = max((float(linha["perda_rs"]) for linha in linhas), default=0)
    return [
        {
            **linha,
            "valor": reais(float(linha["perda_rs"])),
            "largura": round(100 * float(linha["perda_rs"]) / maior) if maior else 0,
        }
        for linha in linhas
    ]


# --- G2 e G3: agenda e criação de OS ----------------------------------------------------------
def listar_insumos(fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, fazenda_id, produto, tipo, unidade, estoque FROM erp.insumos"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY produto, id",
            {"faz": fazenda_id},
        ).fetchall()


def listar_talhoes(fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, fazenda_id, nome, area_ha, cultura FROM erp.talhoes"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY id",
            {"faz": fazenda_id},
        ).fetchall()


def maquinas_livres(inicio: datetime, fim: datetime, talhao_id: int | None = None) -> list[dict]:
    """Pulverizadores livres no intervalo, pela mesma regra do MCP (`disponibilidade_maquinas`):
    com o `talhao_id`, só os da fazenda do talhão."""
    return leitura.disponibilidade_maquinas(inicio, fim, "pulverizador", talhao_id)


def listar_operadores(fazenda_id: int | None = None) -> list[dict]:
    with conectar() as conn:
        return conn.execute(
            "SELECT id, fazenda_id, nome FROM erp.operadores"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) ORDER BY nome",
            {"faz": fazenda_id},
        ).fetchall()


# --- G7: conclusão da OS e relatórios ---------------------------------------------------------
def ordens_a_concluir(fazenda_id: int | None = None) -> list[dict]:
    """OS que podem ser concluídas: agendadas ou reagendadas, da mais próxima para a mais distante."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT os.id, os.inicio_previsto, os.duracao_horas, os.dose_por_ha, os.status,
                   t.nome AS talhao, t.area_ha, i.produto, i.unidade, m.nome AS maquina, o.nome AS operador
            FROM erp.ordens_servico os
            JOIN erp.talhoes t ON t.id = os.talhao_id
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            LEFT JOIN erp.maquinas m ON m.id = os.maquina_id
            LEFT JOIN erp.operadores o ON o.id = os.operador_id
            WHERE os.status IN ('agendada', 'reagendada')
              AND (%(faz)s::int IS NULL OR os.fazenda_id = %(faz)s)
            ORDER BY os.inicio_previsto, os.id
            """,
            {"faz": fazenda_id},
        ).fetchall()


def ordem(os_id: int) -> dict | None:
    return next((o for o in ordens_a_concluir() if o["id"] == os_id), None)


def listar_relatorios(fazenda_id: int | None = None) -> list[dict]:
    """A última versão do relatório de cada OS executada (a OS sem relatório aparece para gerar)."""
    with conectar() as conn:
        return conn.execute(
            """
            SELECT os.id AS os_id, t.nome AS talhao, i.produto, e.inicio_real, e.fim_real, e.modalidade,
                   e.area_aplicada_ha, r.id AS relatorio_id, r.versao, r.gerado_em, r.conforme, r.sha256
            FROM erp.execucoes_os e
            JOIN erp.ordens_servico os ON os.id = e.os_id
            JOIN erp.talhoes t ON t.id = os.talhao_id
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            LEFT JOIN LATERAL (
                SELECT id, versao, gerado_em, conforme, sha256 FROM agente.relatorios_aplicacao
                WHERE os_id = os.id ORDER BY versao DESC LIMIT 1
            ) r ON true
            WHERE (%(faz)s::int IS NULL OR os.fazenda_id = %(faz)s)
            ORDER BY e.fim_real DESC, os.id DESC
            """,
            {"faz": fazenda_id},
        ).fetchall()


def pdf_do_relatorio(relatorio_id: int) -> dict | None:
    with conectar() as conn:
        return conn.execute(
            "SELECT os_id, versao, pdf FROM agente.relatorios_aplicacao WHERE id = %s", (relatorio_id,)
        ).fetchone()


# --- F5: indicadores da fazenda ----------------------------------------------------------------
STATUS_DA_OS = ("agendada", "suspensa_preventiva", "reagendada", "executada", "cancelada")
# O estoque "previsto nas OS" soma só as agendadas e reagendadas: a suspensa só consome depois de reagendada.
DIAS_PROXIMAS_OS = 7


def numero(valor: Any, casas: int = 1) -> str:
    """Número no formato brasileiro: 1234.5 → "1.234,5"."""
    inteiro, _, fracao = f"{float(valor):,.{casas}f}".partition(".")
    return inteiro.replace(",", ".") + ("," + fracao if fracao else "")


def indicadores(fazenda_id: int | None = None, agora: datetime | None = None) -> dict[str, Any]:
    """Números do bloco "Fazenda" (F5), todos calculados no banco para a fazenda escolhida.

    Sem `fazenda_id`, soma todas as fazendas. Traz talhões e área, OS por status, OS dos próximos 7 dias,
    insumos com o estoque e o quanto as OS agendadas vão consumir (e quais não cobrem), máquinas em
    manutenção, e por talhão o que está plantado e o próximo plantio planejado.
    """
    agora = agora or datetime.now(FUSO)
    params = {
        "faz": fazenda_id,
        "agora": agora,
        "ate": agora + timedelta(days=DIAS_PROXIMAS_OS),
        "hoje": agora.date(),
    }
    with conectar() as conn:
        talhoes = conn.execute(
            "SELECT count(*) AS n, COALESCE(sum(area_ha), 0) AS ha FROM erp.talhoes"
            " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s)",
            params,
        ).fetchone()
        por_status = {
            linha["status"]: linha["n"]
            for linha in conn.execute(
                "SELECT status, count(*) AS n FROM erp.ordens_servico"
                " WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s) GROUP BY status",
                params,
            )
        }
        proximas = conn.execute(
            """
            SELECT os.id, os.inicio_previsto, os.status, t.nome AS talhao, i.produto, m.nome AS maquina
            FROM erp.ordens_servico os
            JOIN erp.talhoes t ON t.id = os.talhao_id
            LEFT JOIN erp.insumos i ON i.id = os.insumo_id
            LEFT JOIN erp.maquinas m ON m.id = os.maquina_id
            WHERE os.status IN ('agendada', 'reagendada')
              AND os.inicio_previsto >= %(agora)s AND os.inicio_previsto < %(ate)s
              AND (%(faz)s::int IS NULL OR os.fazenda_id = %(faz)s)
            ORDER BY os.inicio_previsto, os.id
            """,
            params,
        ).fetchall()
        insumos = conn.execute(
            """
            SELECT i.id, i.produto, i.tipo, i.unidade, i.estoque, f.nome AS fazenda,
                   COALESCE(sum(t.area_ha * os.dose_por_ha)
                            FILTER (WHERE os.status IN ('agendada', 'reagendada')), 0) AS comprometido,
                   count(os.id) FILTER (WHERE os.status IN ('agendada', 'reagendada')) AS os_agendadas
            FROM erp.insumos i
            JOIN erp.fazendas f ON f.id = i.fazenda_id
            LEFT JOIN erp.ordens_servico os ON os.insumo_id = i.id
            LEFT JOIN erp.talhoes t ON t.id = os.talhao_id
            WHERE (%(faz)s::int IS NULL OR i.fazenda_id = %(faz)s)
            GROUP BY i.id, f.nome
            ORDER BY f.nome, i.produto, i.id
            """,
            params,
        ).fetchall()
        maquinas = conn.execute(
            "SELECT count(*) AS total, count(*) FILTER (WHERE status = 'manutencao') AS manutencao,"
            " COALESCE(string_agg(nome, ', ' ORDER BY nome)"
            "          FILTER (WHERE status = 'manutencao'), '') AS nomes"
            " FROM erp.maquinas WHERE (%(faz)s::int IS NULL OR fazenda_id = %(faz)s)",
            params,
        ).fetchone()
        plantio = conn.execute(
            """
            SELECT t.id, t.nome, t.area_ha, t.cultura, t.data_plantio, t.estagio_fenologico,
                   f.nome AS fazenda, p.safra AS proxima_safra, p.cultura AS proxima_cultura,
                   p.plantio_previsto
            FROM erp.talhoes t
            JOIN erp.fazendas f ON f.id = t.fazenda_id
            LEFT JOIN LATERAL (
                SELECT safra, cultura, plantio_previsto FROM erp.planejamento_safra
                WHERE talhao_id = t.id AND status = 'planejado' AND plantio_previsto >= %(hoje)s
                ORDER BY plantio_previsto LIMIT 1
            ) p ON true
            WHERE (%(faz)s::int IS NULL OR t.fazenda_id = %(faz)s)
            ORDER BY f.nome, t.id
            """,
            params,
        ).fetchall()
    for i in insumos:
        i["saldo"] = float(i["estoque"]) - float(i["comprometido"])
        i["cobre"] = i["saldo"] >= 0
    for t in plantio:
        t["dias_de_plantio"] = (agora.date() - t["data_plantio"]).days if t["data_plantio"] else None
        t["dias_ate_plantar"] = (t["plantio_previsto"] - agora.date()).days if t["plantio_previsto"] else None
    return {
        "talhoes": talhoes["n"],
        "area_ha": float(talhoes["ha"]),
        "os_por_status": {s: por_status.get(s, 0) for s in STATUS_DA_OS},
        "os_proximas": proximas,
        "dias_proximas": DIAS_PROXIMAS_OS,
        "insumos": insumos,
        "insumos_sem_cobertura": [i for i in insumos if not i["cobre"]],
        "maquinas": dict(maquinas),
        "plantio": plantio,
    }
