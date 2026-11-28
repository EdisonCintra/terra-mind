"""Tela "Hoje" e tela do incidente (conceito 2 do Figma): monta, a partir do banco e do Monitor, o que os
templates mostram. Funções puras: recebem os dados prontos e devolvem dicionários; nada de SQL nem HTTP aqui.

Regra da casa: só número que existe. O que o Figma mostra e o sistema ainda não calcula fica de fora.
"""

from datetime import datetime, timedelta
from typing import Any

from painel.dados import FUSO

DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
EXTENSO = {
    True: ["nenhuma", "uma", "duas", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez"],
    False: ["nenhum", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito", "nove", "dez"],
}
INICIO_DIA_H = 6  # a régua começa às 06h de hoje e vai até as 18h de amanhã (36 h)
HORAS_REGUA = 36
CHUVA_MIN_MM = 0.2  # abaixo disso a hora não pinta a régua (garoa de previsão)

# estado de aplicação (Figma "Selo de aplicação") -> (rótulo, tom)
PODE_APLICAR = ("Pode aplicar", "bom")
ATENCAO = ("Atenção", "atencao")
SUSPENSA = ("Suspensa", "risco")


def por_extenso(n: int, feminino: bool = True, maiuscula: bool = False) -> str:
    """2 → "duas"; acima de dez fica o algarismo."""
    texto = EXTENSO[feminino][n] if 0 <= n <= 10 else str(n)
    return texto[:1].upper() + texto[1:] if maiuscula else texto


def quando(ts: datetime | str | None, agora: datetime) -> str:
    """ "hoje 07h", "amanhã 06h" ou "sex 20/11, 07h"."""
    if not ts:
        return "—"
    ts = (datetime.fromisoformat(ts) if isinstance(ts, str) else ts).astimezone(FUSO)
    hoje = agora.astimezone(FUSO).date()
    dia = {hoje: "hoje", hoje + timedelta(days=1): "amanhã"}.get(ts.date())
    hora = f"{ts:%H}h" if ts.minute == 0 else f"{ts:%H:%M}"
    return f"{dia} {hora}" if dia else f"{DIAS[ts.weekday()]} {ts:%d/%m}, {hora}"


def quando_frase(ts: datetime | str | None, agora: datetime) -> str:
    """ "amanhã às 06h", "hoje às 10h" ou "sex 20/11 às 07h" (para frases: "Remarcar para amanhã às 06h")."""
    texto = quando(ts, agora)
    if texto == "—":
        return texto
    dia, _, hora = texto.replace(",", "").rpartition(" ")
    return f"{dia} às {hora}"


def reais_curto(valor: float) -> str:
    """R$ sem centavos para os números grandes dos cartões: 4800.0 → "R$ 4.800"."""
    return "R$ " + f"{round(valor):,}".replace(",", ".")


def _dt(valor: Any) -> datetime | None:
    if isinstance(valor, datetime):
        return valor.astimezone(FUSO)
    if isinstance(valor, str) and valor:
        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00")).astimezone(FUSO)
        except ValueError:
            return None
    return None


def _num(valor: Any, casas: int = 1) -> str:
    """12.0 → "12"; 4.75 → "4,8"; não numérico → "—"."""
    if not isinstance(valor, (int, float)):
        return "—"
    texto = f"{float(valor):.{casas}f}"
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",")


# --- Plano esperando decisão -----------------------------------------------------------------------
def impactos_dos_eventos(eventos: list[dict]) -> dict[int, dict]:
    """os_id → impacto gravado na auditoria (Ação Preventiva, `suspender_os`): perda se aplicar no horário."""
    impactos: dict[int, dict] = {}
    for e in eventos:
        depois = e.get("depois")
        if isinstance(depois, dict):
            for i in depois.get("impactos") or []:
                if isinstance(i, dict) and "os_id" in i:
                    impactos[i["os_id"]] = i
    return impactos


def montar_plano(
    plano: dict, ordens: dict[int, dict], maquinas: dict[int, str], impactos: dict[int, dict], agora: datetime
) -> dict:
    """Cartão "Plano para aprovar": título, prazo, o que se perde em cada escolha e as OS envolvidas."""
    opcoes = [o for o in plano.get("opcoes") or [] if isinstance(o, dict) and "os_id" in o]
    custos = {c["os_id"]: c for c in plano.get("custos_atraso") or [] if isinstance(c, dict) and "os_id" in c}
    itens = []
    for o in opcoes:
        os_ = ordens.get(o["os_id"]) or {}
        novo = _dt(o.get("novo_inicio"))
        maquina = maquinas.get(o.get("maquina_id")) or os_.get("maquina")
        custo = custos.get(o["os_id"]) or {}
        impacto = impactos.get(o["os_id"]) or {}
        proxima = _dt(custo.get("proxima_janela"))
        itens.append(
            {
                "os_id": o["os_id"],
                "talhao": os_.get("talhao") or f"OS {o['os_id']}",
                "talhao_id": os_.get("talhao_id"),
                "produto": os_.get("produto"),
                "original": quando_frase(os_.get("inicio_previsto"), agora),
                "novo": quando_frase(novo, agora),
                "maquina": maquina,
                "justificativa": o.get("justificativa") or "",
                "perda_rs": impacto.get("perda_rs"),
                "perda": reais_curto(impacto["perda_rs"]) if impacto.get("perda_rs") is not None else None,
                "fonte_perda": impacto.get("fonte_fator"),
                "custo_janela": (
                    reais_curto(custo["custo_perder_janela_rs"])
                    if custo.get("custo_perder_janela_rs") is not None
                    else None
                ),
                "estimativa": str(custo.get("fonte_fator", "")).startswith("estimativa"),
                "proxima": quando(proxima, agora) if proxima else None,
                "area_ha": os_.get("area_ha"),
                "cultura": os_.get("cultura"),
            }
        )
    vencimentos = [v for v in (_dt(c.get("vence_em")) for c in custos.values()) if v]
    vence = min(vencimentos) if vencimentos else None
    if len(itens) == 1:
        i = itens[0]
        titulo = f"Remarcar o {i['talhao']} para {i['novo']}" + (
            f", no {i['maquina']}" if i["maquina"] else ""
        )
        resumo = (
            f"Plano INC-{plano['incidente_id']:04d} para a aplicação de {i['produto'] or 'insumo'}"
            f" marcada para {i['original']}."
        )
    else:
        titulo = (
            f"Remarcar {por_extenso(len(itens), False)} ordens de serviço" if itens else "Plano sem opções"
        )
        resumo = (
            f"Plano INC-{plano['incidente_id']:04d}: "
            + "; ".join(f"{i['talhao']} para {i['novo']}" for i in itens)
            + "."
        )
    perdas = [i["perda_rs"] for i in itens if i["perda_rs"] is not None]
    return {
        "id": plano["id"],
        "incidente_id": plano["incidente_id"],
        "escalado": bool(plano.get("escalado_em")),
        "mensagem": plano.get("mensagem") or "",
        "titulo": titulo,
        "resumo": resumo,
        "itens": itens,
        "vence": vence,
        "vencido": bool(vence and agora >= vence),
        "prazo": (
            f"Decida até {quando(vence, agora).replace('hoje ', '')}"
            if vence and agora < vence
            else "Janela vencida: peça outra opção"
            if vence
            else None
        ),
        "perda_total": reais_curto(sum(perdas)) if perdas else None,
    }


# --- Aplicações de hoje e amanhã ------------------------------------------------------------------
def inicio_regua(agora: datetime) -> datetime:
    return agora.astimezone(FUSO).replace(hour=INICIO_DIA_H, minute=0, second=0, microsecond=0)


def _pct(ts: datetime, inicio: datetime, horas: int) -> float:
    return round(100 * (ts - inicio).total_seconds() / (horas * 3600), 2)


def regua(
    inicio: datetime, horas: int, previsao: list | None, detalhes: list, agendado: datetime | None, tom: str
) -> dict:
    """Uma linha do tempo de `horas` a partir de `inicio`: chuva prevista, faixas fora do limite (do /risco
    do Monitor) e a marca do horário agendado. Posições em % da largura."""
    fim = inicio + timedelta(hours=horas)
    chuva = []
    for h in previsao or []:
        if not isinstance(h, dict):
            continue
        ts, mm = _dt(h.get("ts")), h.get("chuva_mm")
        if ts and inicio <= ts < fim and isinstance(mm, (int, float)) and mm >= CHUVA_MIN_MM:
            chuva.append(
                {
                    "esq": _pct(ts, inicio, horas),
                    "larg": round(100 / horas, 2),
                    "mm": _num(mm),
                    "hora": f"{ts:%H}h",
                }
            )
    foras = []
    for d in detalhes:
        a, b = _dt(d.get("inicio")), _dt(d.get("fim"))
        if not a or not b or d.get("parametro") == "chuva":
            continue
        a, b = max(a, inicio), min(b + timedelta(hours=1), fim)
        if a < b:
            foras.append(
                {
                    "esq": _pct(a, inicio, horas),
                    "larg": _pct(b, inicio, horas) - _pct(a, inicio, horas),
                    "tom": "risco" if d.get("nivel") == "vermelho" else "atencao",
                    "titulo": d.get("rotulo") or d.get("parametro") or "",
                }
            )
    marca = None
    if agendado and inicio <= agendado < fim:
        marca = {"esq": _pct(agendado, inicio, horas), "tom": tom}
    meia_noite = (inicio + timedelta(days=1)).replace(hour=0)
    return {"chuva": chuva, "foras": foras, "marca": marca, "divisor": _pct(meia_noite, inicio, horas)}


def _sobrepoe(d: dict, inicio: datetime, fim: datetime) -> bool:
    a, b = _dt(d.get("inicio")), _dt(d.get("fim"))
    return bool(a and b and a < fim and b + timedelta(hours=1) > inicio)


def _hora_da_previsao(previsao: list | None, ts: datetime) -> dict | None:
    for h in previsao or []:
        if isinstance(h, dict) and _dt(h.get("ts")) == ts.replace(minute=0, second=0, microsecond=0):
            return h
    return None


def _texto_detalhe(d: dict) -> str:
    rotulo = d.get("rotulo") or d.get("parametro") or "limite"
    un = d.get("unidade") or ""
    unidade = un if un == "%" else f" {un}" if un else ""
    a = _dt(d.get("inicio"))
    hora = f" às {a:%H}h" if a else ""
    if d.get("sentido") == "evento":
        return f"{rotulo} provável{hora}."
    valor, limite = d.get("valor"), d.get("limite")
    texto = f"{rotulo} de {_num(valor)}{unidade}{hora}"
    if limite is not None:
        texto += f"; o limite é {_num(limite)}{unidade}"
    return texto + "."


def estado_da_os(os_: dict, risco: dict | None, previsao: list | None, risco_disponivel: bool) -> dict:
    """Selo, motivo e tom da linha de uma OS: suspensa, atenção (algum limite passa no horário) ou pode aplicar."""
    inicio = os_["inicio_previsto"].astimezone(FUSO)
    fim = inicio + timedelta(hours=float(os_.get("duracao_horas") or 1))
    detalhes = [d for d in (risco or {}).get("detalhes") or [] if isinstance(d, dict)]
    no_horario = [d for d in detalhes if _sobrepoe(d, inicio, fim)]
    if os_["status"] == "suspensa_preventiva":
        motivo = _texto_detalhe(no_horario[0]) if no_horario else "Suspensa pelo risco de clima no horário."
        return {"selo": SUSPENSA, "motivo": motivo}
    if not risco_disponivel or risco is None:
        return {"selo": None, "motivo": "Sem previsão para conferir o horário."}
    if no_horario:
        return {"selo": ATENCAO, "motivo": _texto_detalhe(no_horario[0])}
    hora = _hora_da_previsao(previsao, inicio)
    if hora and isinstance(hora.get("vento_kmh"), (int, float)):
        partes = [f"Vento de {_num(hora['vento_kmh'], 0)} km/h"]
        if isinstance(hora.get("delta_t_c"), (int, float)):
            partes.append(f"Delta T {_num(hora['delta_t_c'])}")
        motivo = " e ".join(partes) + f" às {inicio:%H}h, dentro dos limites."
    else:
        motivo = "Sem alerta de clima no horário."
    return {"selo": PODE_APLICAR, "motivo": motivo}


def montar_aplicacoes(
    ordens: list[dict],
    risco_por_talhao: dict[int, dict],
    previsoes: dict[int, list | None],
    risco_disponivel: bool,
    plano_da_os: dict[int, int],
    agora: datetime,
) -> list[dict]:
    """OS agendadas, reagendadas ou suspensas que começam entre 06h de hoje e 18h de amanhã."""
    inicio = inicio_regua(agora)
    fim = inicio + timedelta(hours=HORAS_REGUA)
    linhas = []
    for os_ in ordens:
        if os_["status"] not in ("agendada", "reagendada", "suspensa_preventiva"):
            continue
        ts = os_["inicio_previsto"].astimezone(FUSO)
        if not inicio <= ts < fim:
            continue
        risco = risco_por_talhao.get(os_.get("talhao_id"))
        previsao = previsoes.get(os_.get("talhao_id"))
        estado = estado_da_os(os_, risco, previsao, risco_disponivel)
        tom = estado["selo"][1] if estado["selo"] else "neutro"
        detalhes = [d for d in (risco or {}).get("detalhes") or [] if isinstance(d, dict)]
        linhas.append(
            {
                **os_,
                **estado,
                "quando": quando(ts, agora),
                "regua": regua(inicio, HORAS_REGUA, previsao, detalhes, ts, tom),
                "incidente_id": plano_da_os.get(os_["id"]),
            }
        )
    return linhas


def eixo_regua(agora: datetime) -> list[dict]:
    inicio = inicio_regua(agora)
    return [
        {"esq": round(100 * h / HORAS_REGUA, 2), "hora": f"{(inicio + timedelta(hours=h)):%H}h"}
        for h in range(0, HORAS_REGUA + 1, 6)
    ]


# --- Minimapa dos talhões --------------------------------------------------------------------------
NIVEL = {"verde": "bom", "amarelo": "atencao", "vermelho": "risco"}


def _aneis(geom: Any) -> list[list]:
    if isinstance(geom, dict) and geom.get("type") == "Feature":
        geom = geom.get("geometry")
    if not isinstance(geom, dict):
        return []
    if geom.get("type") == "Polygon":
        return geom.get("coordinates", [])[:1]
    if geom.get("type") == "MultiPolygon":
        return [p[0] for p in geom.get("coordinates", []) if p]
    return []


def mini_mapa(
    talhoes: list[dict], risco_por_talhao: dict[int, dict], largura: int = 300, altura: int = 170
) -> dict:
    """Polígonos dos talhões projetados num SVG pequeno (equirretangular, basta numa fazenda), coloridos pelo
    risco das próximas 48 h. Talhão sem polígono vira um círculo no lat/lon."""
    import json
    import math

    formas = []
    for t in talhoes:
        geom = t.get("geom")
        if isinstance(geom, str):
            try:
                geom = json.loads(geom)
            except ValueError:
                geom = None
        aneis = [[(float(x), float(y)) for x, y, *_ in anel] for anel in _aneis(geom)]
        formas.append((t, aneis))
    pontos = [p for _, aneis in formas for anel in aneis for p in anel] + [
        (float(t["lon"]), float(t["lat"])) for t, aneis in formas if not aneis and t.get("lat") is not None
    ]
    if not pontos:
        return {"formas": [], "largura": largura, "altura": altura}
    xs, ys = [p[0] for p in pontos], [p[1] for p in pontos]
    lat_media = sum(ys) / len(ys)
    kx = math.cos(math.radians(lat_media))
    w = max((max(xs) - min(xs)) * kx, 1e-4)
    h = max(max(ys) - min(ys), 1e-4)
    margem = 12
    escala = min((largura - 2 * margem) / w, (altura - 2 * margem) / h)
    dx = (largura - w * escala) / 2
    dy = (altura - h * escala) / 2

    def xy(lon: float, lat: float) -> tuple[float, float]:
        return round(dx + (lon - min(xs)) * kx * escala, 1), round(dy + (max(ys) - lat) * escala, 1)

    saida = []
    for t, aneis in formas:
        nivel = (risco_por_talhao.get(t["id"]) or {}).get("nivel")
        tom = NIVEL.get(nivel, "neutro")
        if aneis:
            caminho = " ".join(
                "M" + " L".join(f"{a},{b}" for a, b in (xy(*p) for p in anel)) + " Z" for anel in aneis
            )
            todos = [xy(*p) for anel in aneis for p in anel]
            cx = sum(p[0] for p in todos) / len(todos)
            cy = sum(p[1] for p in todos) / len(todos)
            saida.append(
                {
                    "id": t["id"],
                    "nome": t["nome"],
                    "caminho": caminho,
                    "x": round(cx, 1),
                    "y": round(cy, 1),
                    "tom": tom,
                    "rotulo": rotulo_curto(t["nome"]),
                }
            )
        elif t.get("lat") is not None:
            x, y = xy(float(t["lon"]), float(t["lat"]))
            saida.append(
                {
                    "id": t["id"],
                    "nome": t["nome"],
                    "caminho": None,
                    "x": x,
                    "y": y,
                    "tom": tom,
                    "rotulo": rotulo_curto(t["nome"]),
                }
            )
    return {"formas": saida, "largura": largura, "altura": altura}


def rotulo_curto(nome: str) -> str:
    """ "Talhão 3 - Baixada" → "3" (o número do talhão no minimapa); sem número, as duas primeiras letras."""
    import re

    achado = re.search(r"\d+", nome or "")
    return achado.group() if achado else (nome or "?")[:2]


def contagem_risco(talhoes: list[dict], risco_por_talhao: dict[int, dict]) -> dict[str, int]:
    conta = {"risco": 0, "atencao": 0, "bom": 0, "neutro": 0}
    for t in talhoes:
        conta[NIVEL.get((risco_por_talhao.get(t["id"]) or {}).get("nivel"), "neutro")] += 1
    return conta


# --- Frase do topo ----------------------------------------------------------------------------------
def resumo_do_dia(planos: list[dict], aplicacoes: list[dict], agora: datetime) -> tuple[str, str]:
    """(título, subtítulo) do topo da tela Hoje, só com o que está na tela."""
    n = len(planos)
    if n:
        prazos = [p["vence"] for p in planos if p["vence"] and p["vence"] > agora]
        antes = f" antes das {min(prazos):%H}h" if prazos and min(prazos).date() == agora.date() else ""
        titulo = (
            f"{por_extenso(n, maiuscula=True)} {'decisão espera' if n == 1 else 'decisões esperam'}"
            f" você{antes}."
        )
    else:
        titulo = "Nada espera a sua decisão agora."
    if not aplicacoes:
        return titulo, "Nenhuma aplicação marcada até amanhã."
    conta = {"bom": 0, "atencao": 0, "risco": 0, "neutro": 0}
    for a in aplicacoes:
        conta[a["selo"][1] if a["selo"] else "neutro"] += 1
    partes = []
    if conta["bom"]:
        partes.append(f"{por_extenso(conta['bom'])} {'liberada' if conta['bom'] == 1 else 'liberadas'}")
    if conta["atencao"]:
        partes.append(
            f"{por_extenso(conta['atencao'])} {'pede' if conta['atencao'] == 1 else 'pedem'} outro horário"
        )
    if conta["risco"]:
        partes.append(
            f"{por_extenso(conta['risco'])} {'está suspensa' if conta['risco'] == 1 else 'estão suspensas'}"
            " pelo clima"
        )
    if conta["neutro"]:
        partes.append(f"{por_extenso(conta['neutro'])} sem previsão para conferir")
    total = len(aplicacoes)
    lista = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " e " + partes[-1]
    sub = f"{por_extenso(total, maiuscula=True)} {'aplicação' if total == 1 else 'aplicações'} até amanhã: {lista}."
    return titulo, sub
