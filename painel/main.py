"""Painel web do TerraMind: FastAPI + Jinja2 + HTMX servido localmente, sem login nem CDN.

    uv run uvicorn painel.main:app --port 8080

Telas: layout com menu lateral (E1), Visão geral (ordens, previsão e linha do tempo do incidente; a aprovação
fica no canal), Mapa (E4), Histórico (E6), Pergunte à bula (G4, `/bula`), perda evitada (G1, `/perda`), agenda e
janela da OS (G2 e G3, `/agenda`) e relatórios de aplicação (G7, `/relatorios`). `/timeline`, `/aprovacao` e
`/simular` são endereços antigos que voltam para a Visão geral.
"""

import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psycopg
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from painel import dados, escrita
from painel.navegacao import MENU, barra_mobile

log = logging.getLogger("painel")
AQUI = Path(__file__).parent
FUSO = dados.FUSO
FALHA_AGENTS = "Não consegui falar com o serviço de agentes. Tente de novo em instantes."
RESPOSTAS = {"SIM": "Proposta aceita", "NAO": "Proposta recusada", "OPCOES": "Próxima opção solicitada"}
CENARIO = "chuva_80mm.json"  # contrato 14
TALHAO_DEMO = 1  # a OS do cenário está no Talhão 1; sem isso o replay abre um incidente vazio no Talhão 2

app = FastAPI(title="TerraMind · Painel")
app.mount("/static", StaticFiles(directory=AQUI / "static"), name="static")
templates = Jinja2Templates(directory=AQUI / "templates")

# status da OS -> (rótulo, classe CSS); verde agendada, amarelo suspensa, azul reagendada
STATUS_OS = {
    "agendada": ("Agendada", "agendada"),
    "suspensa_preventiva": ("Suspensa", "suspensa"),
    "reagendada": ("Reagendada", "reagendada"),
    "executada": ("Executada", "executada"),
    "cancelada": ("Cancelada", "cancelada"),
}
STATUS_INCIDENTE = {
    "aberto": "Aberto",
    "aguardando_aprovacao": "Aguardando aprovação",
    "aguardando_decisao_manual": "Decisão manual",
    "encerrado": "Encerrado",
}


def _hora(valor: datetime | None) -> str:
    return valor.astimezone(FUSO).strftime("%d/%m %H:%M") if valor else "—"


def _hms(valor: datetime | None) -> str:
    return valor.astimezone(FUSO).strftime("%H:%M:%S") if valor else "—"


def _dia(valor: date | str) -> str:
    """ "qua 18/11" a partir de uma data ou de um ISO (a agenda chega do `agents` em JSON)."""
    valor = date.fromisoformat(valor[:10]) if isinstance(valor, str) else valor
    return f"{DIAS[valor.weekday()]} {valor:%d/%m}"


def _quando(valor: datetime | str | None) -> str:
    """ "sáb 23/11, 18h" a partir de um datetime ou de um ISO vindo do Monitor; "hoje"/"amanhã" quando cabe."""
    if not valor:
        return "—"
    ts = (datetime.fromisoformat(valor) if isinstance(valor, str) else valor).astimezone(FUSO)
    hoje = _agora().astimezone(FUSO).date()
    dia = {hoje: "hoje", hoje + timedelta(days=1): "amanhã"}.get(ts.date(), f"{DIAS[ts.weekday()]} {ts:%d/%m}")
    return f"{dia}, {ts:%H}h"


def _numero_br(valor: float | int | None) -> str:
    """22.9 → "22,9"; 15.0 → "15"; None → "—"."""
    return "—" if valor is None else f"{round(float(valor), 1):g}".replace(".", ",")


DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
templates.env.filters.update(hora=_hora, hms=_hms, dia=_dia, quando=_quando, num=_numero_br, numero=dados.numero)
templates.env.globals.update(STATUS_OS=STATUS_OS, STATUS_INCIDENTE=STATUS_INCIDENTE)


def cliente_http() -> httpx.Client:
    """Cliente para o `agents` e o Monitor (os testes trocam esta função)."""
    return httpx.Client(timeout=30)


def _url(variavel: str, padrao: str) -> str:
    return os.environ.get(variavel, padrao).rstrip("/")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


def _fazenda(request: Request) -> int | None:
    """Fazenda escolhida no seletor do topo (cookie `fazenda_id`); `None` = todas (contrato 20)."""
    valor = request.cookies.get("fazenda_id", "")
    return int(valor) if valor.isascii() and valor.isdecimal() else None


def contexto_base(request: Request, ativa: str = "/visao-geral") -> dict:
    """Contexto do `layout.html` (menu, fazenda e usuário); toda tela usa e acrescenta o seu."""
    existentes = {r.path for r in request.app.routes}
    usuario = getattr(request.state, "usuario", None)
    papel = usuario.get("papel") if usuario else None
    fazenda = request.cookies.get("fazenda_id", "")
    itens, mais = barra_mobile([
        {"rota": i.rota, "rotulo": i.rotulo, "existe": i.rota in existentes}
        for i in MENU
        if papel is None or papel in i.papeis
    ])
    return {
        "ativa": ativa,
        "itens_menu": itens,
        "menu_mais": mais,
        "fazendas": dados.listar_fazendas(),
        "fazenda_id": int(fazenda) if fazenda.isascii() and fazenda.isdecimal() else None,
        "usuario": usuario,
    }


@app.get("/", include_in_schema=False)
def raiz() -> RedirectResponse:
    return RedirectResponse("/visao-geral")


@app.get("/os", include_in_schema=False)
def os_antiga() -> RedirectResponse:
    """Links antigos (Telegram) apontavam para /os."""
    return RedirectResponse("/visao-geral", status_code=301)


# Telas antigas (T9.3 a T9.5) que viraram cópias da Visão geral: a linha do tempo está nela e a decisão
# é no canal. Os endereços continuam valendo para links antigos.
@app.get("/timeline", include_in_schema=False)
def timeline_antiga(incidente: int | None = None) -> RedirectResponse:
    return RedirectResponse("/visao-geral" + (f"?incidente={incidente}" if incidente is not None else ""))


@app.get("/aprovacao", include_in_schema=False)
@app.get("/simular", include_in_schema=False)
def tela_antiga() -> RedirectResponse:
    return RedirectResponse("/visao-geral")


# R1: rota de tela desconhecida vira página dentro do layout; /api, /parcial e /static seguem em JSON.
# Também seguem em JSON o pedido do HTMX (a página inteira cairia dentro de um bloco) e o 404 que a própria
# rota levanta com motivo (ex.: OS já concluída, PDF inexistente): só endereço sem rota vira página.
SEM_PAGINA_404 = ("/api/", "/parcial/", "/static/")


@app.exception_handler(StarletteHTTPException)
async def pagina_nao_encontrada(request: Request, erro: StarletteHTTPException):
    caminho = request.url.path
    if (erro.status_code != 404 or request.method != "GET" or caminho.startswith(SEM_PAGINA_404)
            or request.headers.get("HX-Request") or erro.detail != "Not Found"):
        return await http_exception_handler(request, erro)
    futura = next((i for i in MENU if i.rota == caminho), None)
    try:  # contexto_base consulta o banco; banco fora do ar não pode transformar o 404 em 500
        contexto = {**contexto_base(request, ativa=caminho), "futura": futura}
        return templates.TemplateResponse(request, "nao_encontrado.html", contexto, status_code=404)
    except Exception:
        log.warning("página 404 de %s sem o layout", caminho, exc_info=True)
        return await http_exception_handler(request, erro)


def _caminho_local(voltar: str) -> bool:
    """Só caminho relativo do painel: um "/" no começo, sem "//", sem "\\" (o navegador lê como "/"),
    sem esquema nem host."""
    partes = urlsplit(voltar)
    return (voltar.startswith("/") and not voltar.startswith("//") and "\\" not in voltar
            and not partes.scheme and not partes.netloc)


@app.post("/fazenda/selecionar", include_in_schema=False)
def selecionar_fazenda(fazenda_id: int = Form(...), voltar: str = Form("/visao-geral")) -> RedirectResponse:
    """Guarda a fazenda escolhida no cookie e volta para a página de origem (só caminhos do próprio painel)."""
    if not _caminho_local(voltar):
        voltar = "/visao-geral"
    resposta = RedirectResponse(voltar, status_code=303)
    if fazenda_id > 0:
        resposta.set_cookie("fazenda_id", str(fazenda_id), max_age=365 * 24 * 3600, samesite="lax")
    else:  # 0 = "Todas as fazendas"
        resposta.delete_cookie("fazenda_id")
    return resposta


# --- Tela 1: visão geral (ordens de serviço) -------------------------------------------------
@app.get("/visao-geral", response_class=HTMLResponse)
def tela_visao_geral(request: Request, incidente: int | None = None):
    """Um seletor de talhão só (bloco Previsão) alimenta cartões de clima e gráfico; começa no talhão
    do incidente escolhido na linha do tempo ou, sem incidente, no primeiro talhão."""
    fazenda = _fazenda(request)
    linha_do_tempo = _contexto_timeline(incidente, fazenda)
    padrao = dados.talhao_padrao(fazenda)
    do_incidente = next(
        (i["talhao_id"] for i in linha_do_tempo["incidentes"] if i["id"] == linha_do_tempo["escolhido"]), None
    )
    contexto = {
        **contexto_base(request),
        **linha_do_tempo,
        "ordens": dados.listar_ordens(fazenda),
        "talhao_padrao": padrao,
        "talhoes": dados.listar_talhoes(fazenda),
        "talhao": do_incidente if do_incidente is not None else padrao,
        "perda": _perda_da_safra(fazenda),
    }
    return templates.TemplateResponse(request, "visao_geral.html", contexto)


@app.get("/componentes", response_class=HTMLResponse, include_in_schema=False)
def vitrine(request: Request):
    """Vitrine dos componentes base, para quem cria telas novas (fora do menu)."""
    return templates.TemplateResponse(request, "vitrine.html", contexto_base(request, ativa="/componentes"))


@app.get("/parcial/os", response_class=HTMLResponse)
def parcial_os(request: Request):
    """Só as `<tr>` da tabela: o HTMX troca o corpo da tabela a cada 2 s."""
    return templates.TemplateResponse(request, "_linhas_os.html", {"ordens": dados.listar_ordens(_fazenda(request))})


@app.get("/parcial/alertas", response_class=HTMLResponse)
def parcial_alertas(request: Request):
    """Faixa vermelha para incidentes em `aguardando_decisao_manual` (telas 1 e 3)."""
    return templates.TemplateResponse(
        request, "_alertas.html", {"manuais": dados.incidentes_decisao_manual(_fazenda(request))}
    )


# --- Tela 2: linha do tempo ------------------------------------------------------------------
def _contexto_timeline(incidente: int | None, fazenda_id: int | None = None) -> dict:
    incidentes = dados.listar_incidentes(fazenda_id=fazenda_id)
    escolhido = incidente if incidente is not None else (incidentes[0]["id"] if incidentes else None)
    eventos = []
    for a in dados.auditoria_do_incidente(escolhido) if escolhido is not None else []:
        texto, citacoes = dados.separar_citacoes(a["justificativa"])
        mudancas = dados.resumo(a["antes"], a["depois"])
        eventos.append({**a, "texto": texto, "citacoes": citacoes, "mudancas": mudancas, "atraso": []})
    # G5: enquanto o gerente não responde, o último pedido de aprovação mostra quando a janela
    # vence e quanto custa perdê-la (recalculado a cada atualização).
    status = next((i["status"] for i in incidentes if i["id"] == escolhido), None)
    pedidos = [e for e in eventos if e["acao"] == "pedir_aprovacao" and isinstance(e["depois"], dict)]
    if status == "aguardando_aprovacao" and pedidos:
        pedidos[-1]["atraso"] = dados.linhas_atraso(pedidos[-1]["depois"].get("custos_atraso"), _agora())
    return {"incidentes": incidentes, "escolhido": escolhido, "eventos": eventos}


def _agora() -> datetime:
    return datetime.now(FUSO)


@app.get("/parcial/seletor", response_class=HTMLResponse)
def parcial_seletor(request: Request, incidente: int | None = None):
    """Links dos incidentes no topo da linha do tempo (o HTMX troca a cada 2 s)."""
    return templates.TemplateResponse(request, "_seletor.html", _contexto_timeline(incidente, _fazenda(request)))


@app.get("/parcial/timeline", response_class=HTMLResponse)
def parcial_timeline(request: Request, incidente: int | None = None):
    return templates.TemplateResponse(request, "_eventos.html", _contexto_timeline(incidente, _fazenda(request)))


# --- Aprovação: a decisão é no canal; estas rotas repassam ao `agents` ----------------------
@app.get("/parcial/pendentes", response_class=HTMLResponse)
def parcial_pendentes(request: Request):
    agora = _agora()
    planos = [
        {**p, "atraso": dados.linhas_atraso(p["custos_atraso"], agora)} for p in dados.planos_pendentes(_fazenda(request))
    ]
    return templates.TemplateResponse(request, "_pendentes.html", {"planos": planos})


@app.post("/aprovacao/{incidente_id}/{plano_id}/{resposta}", response_class=HTMLResponse)
def responder(request: Request, incidente_id: int, plano_id: int, resposta: str):
    """Repassa a decisão ao `agents` (contrato 6/7). 409 = decisão já registrada, não é erro."""
    if resposta not in RESPOSTAS:
        raise HTTPException(422, f"resposta deve ser uma de {', '.join(RESPOSTAS)}")
    url = f"{_url('AGENTS_URL', 'http://localhost:8000')}/incidentes/{incidente_id}/resposta"
    corpo = {"plano_id": plano_id, "resposta": resposta, "respondido_por": "painel"}
    try:
        with cliente_http() as http:
            r = http.post(url, json=corpo)
    except httpx.HTTPError:
        nivel, texto = "erro", FALHA_AGENTS
    else:
        if r.status_code == 409:
            nivel, texto = "aviso", "Decisão já registrada para este plano. Nada foi alterado."
        elif r.status_code == 404:
            nivel, texto = "erro", "Plano não encontrado."
        elif r.is_success:
            nivel, texto = "ok", f"{RESPOSTAS[resposta]}."
        else:
            nivel, texto = "erro", f"O serviço de agentes recusou a resposta (HTTP {r.status_code})."
    return templates.TemplateResponse(request, "_mensagem.html", {"nivel": nivel, "texto": texto})


# --- Pergunte à bula (G4) --------------------------------------------------------------------
TIMEOUT_BULA_S = 150  # o agente pode buscar até 3 vezes e o LLM tem 90 s por chamada


@app.get("/bula", response_class=HTMLResponse)
def tela_bula(request: Request):
    return templates.TemplateResponse(
        request, "bula.html", {**contexto_base(request, "/bula"), "produtos": dados.listar_produtos(_fazenda(request))}
    )


@app.post("/bula/perguntar", response_class=HTMLResponse)
def perguntar_a_bula(request: Request, pergunta: str = Form(""), produto: str = Form("")):
    """Repassa a pergunta ao `agents` e mostra a resposta com trecho, fonte e página."""
    pergunta = pergunta.strip()
    contexto: dict = {"pergunta": pergunta, "produto": produto, "resultado": None, "falha": None}
    if len(pergunta) < 3:
        contexto["falha"] = "Escreva a pergunta."
        return templates.TemplateResponse(request, "_resposta_bula.html", contexto)
    url = f"{_url('AGENTS_URL', 'http://localhost:8000')}/bula/perguntar"
    try:
        with cliente_http() as http:
            r = http.post(
                url, json={"pergunta": pergunta, "produto": produto or None}, timeout=TIMEOUT_BULA_S
            )
    except httpx.HTTPError:
        contexto["falha"] = FALHA_AGENTS
    else:
        if r.is_success:
            contexto["resultado"] = r.json()
        else:
            contexto["falha"] = f"O serviço de agentes recusou a pergunta (HTTP {r.status_code})."
    return templates.TemplateResponse(request, "_resposta_bula.html", contexto)


# --- Previsão do talhão (E2): o gráfico da Visão geral lê o que o Monitor guardou ---------------
@app.get("/api/previsao/{talhao_id}")
def api_previsao(talhao_id: int) -> JSONResponse:
    """Repassa a previsão do Monitor (e os cabeçalhos de fonte); Monitor fora do ar ou com erro = 503."""
    url = f"{_url('MONITOR_URL', 'http://localhost:8020')}/previsao/{talhao_id}"
    try:
        with cliente_http() as http:
            r = http.get(url)
        corpo = r.json() if r.status_code == 200 or r.status_code == 404 else None
    except (httpx.HTTPError, ValueError):
        corpo = None
        r = None
    if r is None or corpo is None:
        return JSONResponse({"detail": "monitor_indisponivel"}, status_code=503)
    if r.status_code == 404:
        return JSONResponse({"detail": "sem_previsao"}, status_code=404)
    cabecalhos = {k: r.headers[k] for k in ("X-Previsao-Buscada-Em", "X-Previsao-Fonte") if k in r.headers}
    return JSONResponse(corpo, headers=cabecalhos)


# --- Cartões de clima (E3) -------------------------------------------------------------------------
DELTA_T_MIN, DELTA_T_MAX = 2.0, 8.0  # faixa boa de aplicação (°C), igual ao padrão do Monitor
JANELA_INVERSAO_H = 48


def _monitor_json(http: httpx.Client, caminho: str, esperado: type):
    """GET no Monitor; falha, status != 200, JSON ruim ou corpo de outro tipo (list/dict) vira None."""
    try:
        r = http.get(f"{_url('MONITOR_URL', 'http://localhost:8020')}{caminho}")
        corpo = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None
    return corpo if isinstance(corpo, esperado) else None


def _dt(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00")).astimezone(FUSO)


def _numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool)


def _num(valor, unidade: str = "", casas: int = 1) -> str:
    """Número com vírgula decimal; ausente ou não numérico vira traço."""
    return f"{valor:.{casas}f}".replace(".", ",") + unidade if _numero(valor) else "—"


def _rumo(graus) -> str:
    return dados.rumo(graus) if _numero(graus) else "—"


def _talvez(valor, formatar) -> str | None:
    return formatar(valor) if _numero(valor) else None


def _isolado(construir, *args):
    """Monta um cartão; dado malformado apaga só ele (None), nunca derruba a página."""
    try:
        return construir(*args)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return None


def _cartao_delta_t(previsao: list) -> dict:
    dt = previsao[0].get("delta_t_c")
    ok = _numero(dt)
    return {
        "valor": _num(dt, " °C") if ok else None,
        "tom": "bom" if ok and DELTA_T_MIN <= dt <= DELTA_T_MAX else "atencao",
        "faixa": f"faixa boa {DELTA_T_MIN:g}–{DELTA_T_MAX:g} °C",
    }


def _cartao_inversao(previsao: list) -> dict | None:
    janela = previsao[:JANELA_INVERSAO_H]
    marcadas = [h for h in janela if h.get("inversao") is True]
    if marcadas:
        return {"texto": f"risco às {_dt(marcadas[0]['ts']).strftime('%d/%m %Hh')}", "tom": "atencao"}
    if any(h.get("inversao") is False for h in janela):
        return {"texto": "sem risco previsto", "tom": "bom"}
    return None


def _cartao_vento(previsao: list) -> dict:
    agora = previsao[0]
    return {
        "valor": _talvez(agora.get("vento_kmh"), lambda v: _num(v, " km/h", 0)),
        "rajada": _talvez(agora.get("rajada_kmh"), lambda v: _num(v, " km/h", 0)),
        "rumo": _talvez(agora.get("vento_dir_graus"), dados.rumo),
    }


def _faixa_dias(diaria: list) -> list[dict]:
    return [
        {
            "data": datetime.fromisoformat(d["data"]).strftime("%d/%m"),
            "chuva": _num(d.get("chuva_mm"), " mm"),
            "prob": _num(d.get("prob_chuva_pct"), "%", 0),
            "min": _num(d.get("temp_min_c"), "°", 0),
            "max": _num(d.get("temp_max_c"), "°", 0),
            "vento": _num(d.get("vento_max_kmh"), " km/h", 0),
            "rumo": _rumo(d.get("vento_dir_graus")),
        }
        for d in diaria[:7]
    ]


def _leitura(observado: dict) -> dict:
    return {
        **observado,
        "hora": _dt(observado["ts"]).strftime("%d/%m %H:%M"),
        "temp": _num(observado.get("temp_c"), " °C"),
        "umidade": _num(observado.get("umidade_pct"), "%", 0),
        "chuva": _num(observado.get("chuva_mm"), " mm"),
        "vento": _num(observado.get("vento_kmh"), " km/h", 0),
        "rumo": _rumo(observado.get("vento_dir_graus")),
        "distancia": _talvez(observado.get("distancia_km"), lambda v: _num(v, " km")),
    }


def _cartoes(previsao: list | None, diaria: list | None, observado: dict | None) -> dict:
    return {
        "agora": _isolado(_cartao_delta_t, previsao) if previsao else None,
        "inversao": _isolado(_cartao_inversao, previsao) if previsao else None,
        "vento": _isolado(_cartao_vento, previsao) if previsao else None,
        "dias": (_isolado(_faixa_dias, diaria) or None) if diaria else None,
        "observado": _isolado(_leitura, observado) if observado else None,
    }


@app.get("/parcial/clima", response_class=HTMLResponse)
def parcial_clima(request: Request, talhao_id: int | None = None):
    """Cartões de clima do talhão; cada chamada ao Monitor falha sozinha (só o seu cartão fica vazio)."""
    talhao = talhao_id if talhao_id is not None else dados.talhao_padrao(_fazenda(request))
    previsao = diaria = observado = None
    if talhao is not None:
        with cliente_http() as http:
            previsao = _monitor_json(http, f"/previsao/{talhao}", list)
            diaria = _monitor_json(http, f"/previsao/{talhao}/diaria?dias=7", list)
            observado = _monitor_json(http, f"/observado/{talhao}", dict)
    contexto = {"talhao": talhao, **_cartoes(previsao, diaria, observado)}
    return templates.TemplateResponse(request, "_clima_talhao.html", contexto)


# --- Mapa de risco (E4) ------------------------------------------------------------------------------
JANELA_MAPA_H = 48


@app.get("/mapa", response_class=HTMLResponse)
def tela_mapa(request: Request):
    contexto = {**contexto_base(request, "/mapa"), "talhoes": dados.talhoes_com_geometria(_fazenda(request))}
    return templates.TemplateResponse(request, "mapa.html", contexto)


def _risco_do_monitor(fazenda_id: int | None) -> list | None:
    caminho = f"/risco?fazenda_id={fazenda_id}" if fazenda_id is not None else "/risco"
    with cliente_http() as http:
        return _monitor_json(http, caminho, list)


@app.get("/api/risco")
def api_risco(fazenda_id: int | None = None) -> JSONResponse:
    """Repassa o risco por talhão do Monitor; fora do ar ou com erro = 503."""
    risco = _risco_do_monitor(fazenda_id)
    if risco is None:
        return JSONResponse({"detail": "monitor_indisponivel"}, status_code=503)
    return JSONResponse(risco)


def _periodo(inicio: str | None, fim: str | None) -> str:
    """Primeira e última hora em que o motivo aparece (não quer dizer que dure o tempo todo):
    "hoje, 14h–18h" no mesmo dia; "entre hoje, 20h e seg 23/11, 18h" em dias diferentes; "às …" numa hora só."""
    if not inicio or not fim:
        return ""
    a, b = (datetime.fromisoformat(x).astimezone(FUSO) for x in (inicio, fim))
    if a == b:
        return f"às {_quando(a)}"
    if a.date() == b.date():
        return f"{_quando(a)}–{b:%H}h"
    return f"entre {_quando(a)} e {_quando(b)}"


def _linha_risco(d: dict) -> dict:
    """Um motivo do /risco em partes legíveis: rótulo, valor em destaque, limite e período."""
    un = d.get("unidade") or ""
    unidade = un if un == "%" else f" {un}" if un else ""
    valor, limite = d.get("valor"), d.get("limite")
    sentido = d.get("sentido", "max")
    if sentido == "evento":
        destaque, comparacao = "provável", "pode segurar a deriva perto do chão"
    else:
        destaque = f"{_numero_br(valor)}{unidade}"
        prefixo = {"total": "no total · limite", "min": "mínimo · limite", "max": "pico · limite"}[sentido]
        comparacao = f"{prefixo} {_numero_br(limite)}{unidade}" if limite is not None else ""
    return {"rotulo": d.get("rotulo") or d.get("parametro", ""), "destaque": destaque, "comparacao": comparacao,
            "periodo": _periodo(d.get("inicio"), d.get("fim")),
            "tom": "risco" if d.get("nivel") == "vermelho" else "atencao"}


@app.get("/parcial/mapa/talhao/{talhao_id}", response_class=HTMLResponse)
def parcial_mapa_talhao(request: Request, talhao_id: int):
    """Detalhe do clique no mapa: motivos do risco e as OS do talhão nas próximas 48 h."""
    risco = _risco_do_monitor(None)
    do_talhao = next((r for r in risco or [] if isinstance(r, dict) and r.get("talhao_id") == talhao_id), None)
    agora = _agora()
    ordens = dados.ordens_do_talhao(talhao_id, agora, agora + timedelta(hours=JANELA_MAPA_H))
    linhas = [_linha_risco(d) for d in (do_talhao or {}).get("detalhes") or [] if isinstance(d, dict)]
    contexto = {"risco": do_talhao, "risco_indisponivel": risco is None, "ordens": ordens, "linhas": linhas}
    return templates.TemplateResponse(request, "_talhao_risco.html", contexto)


# --- Histórico (E6) --------------------------------------------------------------------------------
ACERTO_BOM, ACERTO_ATENCAO = 80.0, 50.0  # % de suspensões corretas; sem conferência o KPI fica neutro


def _tom_acerto(pct: float | None) -> str:
    if pct is None:
        return "neutro"
    return "bom" if pct >= ACERTO_BOM else "atencao" if pct >= ACERTO_ATENCAO else "risco"


@app.get("/historico", response_class=HTMLResponse)
def tela_historico(request: Request):
    fazenda = contexto_base(request, "/historico")
    resumo = dados.resumo_historico(fazenda["fazenda_id"])
    pct, erro = resumo["pct_corretas"], resumo["erro_medio_mm"]
    contexto = {
        **fazenda,
        "resumo": resumo,
        "pct_tom": _tom_acerto(pct),
        "pct_texto": _num(pct, "%") if pct is not None else "—",
        "erro_texto": _num(erro, " mm") if erro is not None else "—",
        "usa_reanalise": any(i["reanalise"] for i in resumo["itens"]),
    }
    return templates.TemplateResponse(request, "historico.html", contexto)


# --- Simular chuva (botão da Visão geral) ----------------------------------------------------
@app.post("/simular/chuva", response_class=HTMLResponse)
def simular_chuva(request: Request):
    """Dispara o replay no Monitor (contrato 14) e mostra `incidentes` e `erros` sem quebrar a página."""
    url = f"{_url('MONITOR_URL', 'http://localhost:8020')}/replay"
    contexto: dict = {"incidentes": [], "erros": [], "falha": None}
    try:
        with cliente_http() as http:
            talhao = int(os.environ.get("TALHAO_DEMO", TALHAO_DEMO))
            r = http.post(url, json={"cenario": CENARIO, "talhao_id": talhao})
    except httpx.HTTPError:
        contexto["falha"] = "Não consegui falar com o Monitor."
    else:
        if r.is_success:
            try:
                corpo = r.json()
                contexto["incidentes"] = corpo.get("incidentes") or []
                contexto["erros"] = corpo.get("erros") or []
            except ValueError:
                contexto["falha"] = "Resposta inesperada do Monitor."
        elif r.status_code == 404:
            contexto["falha"] = f"Cenário {CENARIO} não encontrado no Monitor."
        else:
            contexto["falha"] = f"O Monitor recusou o pedido (HTTP {r.status_code})."
    return templates.TemplateResponse(request, "_simulacao.html", contexto)


# --- Chamadas ao `agents` usadas pelas telas novas --------------------------------------------
def _agents(metodo: str, caminho: str, timeout: float = 30, **kwargs) -> tuple[dict | None, str | None]:
    """(corpo JSON, falha). A falha já vem como texto para a tela; nunca levanta."""
    url = f"{_url('AGENTS_URL', 'http://localhost:8000')}{caminho}"
    try:
        with cliente_http() as http:
            r = http.request(metodo, url, timeout=timeout, **kwargs)
    except httpx.HTTPError:
        return None, FALHA_AGENTS
    if r.is_success:
        return r.json(), None
    try:
        detalhe = r.json().get("detail")
    except ValueError:
        detalhe = None
    return None, str(detalhe or f"O serviço de agentes recusou o pedido (HTTP {r.status_code}).")


def _mensagem(request: Request, nivel: str, texto: str, **cabecalhos: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_mensagem.html", {"nivel": nivel, "texto": texto}, headers=cabecalhos
    )


# --- Perda evitada (G1) ----------------------------------------------------------------------
def _perda_da_safra(fazenda_id: int | None = None) -> dict | None:
    """Total da safra para o KPI da Visão geral: o mesmo número da tela /perda (de `agente.impactos`,
    simulações fora). None se o banco ainda não tem a 020 ou está fora: o KPI mostra traço, neutro."""
    hoje = _agora().date()
    try:
        p = dados.perda_evitada(dados.inicio_da_safra(hoje), hoje, fazenda_id=fazenda_id)
    except psycopg.Error:
        log.warning("perda evitada indisponível", exc_info=True)
        return None
    return {"total_rs": p["total_rs"], "total": p["total"]}


@app.get("/api/perda-evitada")
def api_perda_evitada(request: Request) -> JSONResponse:
    """Total da safra para o painel.js atualizar o KPI sem recarregar; o tom segue este valor."""
    perda = _perda_da_safra(_fazenda(request))
    if perda is None:
        return JSONResponse({"detail": "perda_indisponivel"}, status_code=503)
    return JSONResponse(perda)


@app.get("/perda", response_class=HTMLResponse)
def tela_perda(request: Request, inicio: date | None = None, fim: date | None = None, replay: bool = False):
    """Relatório de fim de safra: tudo vem de `agente.impactos`; incidente de replay só com `replay=1`."""
    hoje = _agora().date()
    inicio = inicio or dados.inicio_da_safra(hoje)
    fim = fim or hoje
    contexto = {
        **contexto_base(request, "/perda"),
        "p": dados.perda_evitada(inicio, fim, replay, _fazenda(request)),
        "replay": replay,
    }
    return templates.TemplateResponse(request, "perda.html", contexto)


# --- Fazenda: indicadores (F5) ---------------------------------------------------------------
@app.get("/fazenda", response_class=HTMLResponse)
def tela_fazenda(request: Request):
    """Talhões, OS por status, estoque, máquinas e o que está (e vai ser) plantado, da fazenda escolhida."""
    fazenda = _fazenda(request)
    nome = next((f["nome"] for f in dados.listar_fazendas() if f["id"] == fazenda), None)
    contexto = {**contexto_base(request, "/fazenda"), "i": dados.indicadores(fazenda, _agora()), "fazenda": nome}
    return templates.TemplateResponse(request, "indicadores.html", contexto)


# --- Agenda e janela da OS (G2 e G3) ---------------------------------------------------------
HORAS_AGENDA = range(6, 19)  # horário de trabalho das janelas (agents.rules.janelas)


def _celulas(dia: dict) -> list[dict]:
    """Uma célula por hora do dia de trabalho: boa, ruim (com o motivo) ou sem previsão."""
    celulas = {h: {"hora": h, "classe": "sem", "titulo": "sem previsão"} for h in HORAS_AGENDA}
    for faixa in dia["faixas"]:
        inicio = datetime.fromisoformat(faixa["inicio"]).astimezone(FUSO)
        fim = datetime.fromisoformat(faixa["fim"]).astimezone(FUSO)
        horas = int((fim - inicio).total_seconds() // 3600)
        for h in range(inicio.hour, inicio.hour + horas):
            if h not in celulas:
                continue
            if faixa["serve"]:
                celulas[h] |= {"classe": "boa", "titulo": f"{h}h: boa para aplicar"}
            else:
                passou = faixa.get("parametro") == "horario"
                fonte = f" ({faixa['fonte']})" if faixa.get("fonte") else ""
                celulas[h] |= {
                    "classe": "passou" if passou else "ruim",
                    "titulo": f"{h}h: {faixa.get('motivo') or 'não serve'}{fonte}",
                }
    return list(celulas.values())


def _motivos(dia: dict) -> list[str]:
    """Os motivos das faixas ruins do dia, sem repetir e sem as horas que já passaram."""
    vistos: dict[str, str] = {}
    for faixa in dia["faixas"]:
        if faixa["serve"] or faixa.get("parametro") == "horario":
            continue
        inicio = datetime.fromisoformat(faixa["inicio"]).astimezone(FUSO)
        fim = datetime.fromisoformat(faixa["fim"]).astimezone(FUSO)
        fonte = f" ({faixa['fonte']})" if faixa.get("fonte") else ""
        vistos.setdefault(faixa["parametro"], f"{inicio:%Hh}–{fim:%Hh}: {faixa['motivo']}{fonte}")
    return list(vistos.values())


@app.get("/agenda", response_class=HTMLResponse)
def tela_agenda(request: Request):
    contexto = {
        **contexto_base(request, "/agenda"),
        "insumos": dados.listar_insumos(_fazenda(request)),
        "talhoes": dados.listar_talhoes(_fazenda(request)),
        "operadores": dados.listar_operadores(_fazenda(request)),
        "horas": list(HORAS_AGENDA),
    }
    return templates.TemplateResponse(request, "agenda.html", contexto)


@app.get("/parcial/agenda", response_class=HTMLResponse)
def parcial_agenda(request: Request, insumo_id: int, duracao_horas: float = 8, dias: int = 7):
    params = {"insumo_id": insumo_id, "duracao_horas": duracao_horas, "dias": dias}
    if (fazenda := _fazenda(request)) is not None:
        params["fazenda_id"] = fazenda  # o agents só devolve os talhões da fazenda do insumo
    agenda, falha = _agents("GET", "/agenda", params=params)
    if agenda:
        for talhao in agenda["talhoes"]:
            for dia in talhao["dias"]:
                dia["celulas"], dia["motivos"] = _celulas(dia), _motivos(dia)
    contexto = {"agenda": agenda, "falha": falha, "horas": list(HORAS_AGENDA)}
    return templates.TemplateResponse(request, "_agenda.html", contexto)


@app.post("/agenda/limites", response_class=HTMLResponse)
def ler_limites_da_bula(request: Request, produto: str = Form(...)):
    """Pede ao `agents` a leitura da bula (contrato 26). Demora: o agente busca e o código confere."""
    resultado, falha = _agents("POST", "/limites/extrair", timeout=TIMEOUT_BULA_S, json={"produto": produto})
    if falha or resultado.get("erro"):
        return _mensagem(request, "erro", falha or f"Não consegui ler a bula agora ({resultado['erro']}).")
    if not resultado["limites"]:
        return _mensagem(
            request, "aviso", f"A bula de {produto} não traz limite de clima que eu consiga citar."
        )
    paginas = ", ".join(sorted({f"p. {x['pagina']}" for x in resultado["limites"]}))
    texto = f"{len(resultado['limites'])} limite(s) lido(s) da bula de {produto} ({paginas})."
    return _mensagem(request, "ok", texto, **{"HX-Trigger": "agenda-mudou"})


def _inicio_local(texto: str) -> datetime | None:
    """`datetime-local` do formulário ("2026-11-18T14:00") no fuso da fazenda."""
    try:
        return datetime.fromisoformat(texto).replace(tzinfo=FUSO)
    except ValueError:
        return None


@app.post("/agenda/avaliar", response_class=HTMLResponse)
def avaliar_janela(
    request: Request,
    talhao_id: int = Form(...),
    insumo_id: int = Form(...),
    inicio: str = Form(...),
    duracao_horas: float = Form(8),
):
    """G3: ao escolher a data, mostra se a janela serve, o motivo e as sugestões com máquina livre."""
    quando = _inicio_local(inicio)
    if quando is None:
        return _mensagem(request, "erro", "Escolha a data e a hora de início.")
    corpo = {
        "talhao_id": talhao_id,
        "insumo_id": insumo_id,
        "inicio": quando.isoformat(),
        "duracao_horas": duracao_horas,
    }
    avaliacao, falha = _agents("POST", "/janelas/avaliar", json=corpo)
    if falha:
        return _mensagem(request, "erro", falha)
    for s in avaliacao["sugestoes"]:
        s["valor"] = datetime.fromisoformat(s["inicio"]).astimezone(FUSO).strftime("%Y-%m-%dT%H:%M")
    livres = (
        dados.maquinas_livres(quando, quando + timedelta(hours=duracao_horas), talhao_id)
        if avaliacao["serve"]
        else []
    )
    return templates.TemplateResponse(request, "_janela.html", {"a": avaliacao, "maquinas": livres})


@app.post("/os/nova", response_class=HTMLResponse)
def criar_os(
    request: Request,
    talhao_id: int = Form(...),
    insumo_id: int = Form(...),
    inicio: str = Form(...),
    duracao_horas: float = Form(8),
    dose_por_ha: float = Form(...),
    maquina_id: int = Form(...),
    operador_id: str = Form(""),
):
    quando = _inicio_local(inicio)
    if quando is None or dose_por_ha <= 0 or duracao_horas <= 0:
        return _mensagem(request, "erro", "Confira a data, a dose e a duração.")
    r = escrita.criar_os(
        talhao_id,
        insumo_id,
        dose_por_ha,
        quando,
        duracao_horas,
        maquina_id,
        int(operador_id) if operador_id else None,
    )
    if not r["ok"]:
        return _mensagem(request, "erro", r["erro"])
    return _mensagem(
        request, "ok", f"OS {r['os']['id']} criada para {_hora(quando)}.", **{"HX-Trigger": "os-criada"}
    )


# --- Relatórios de aplicação (G7) ------------------------------------------------------------
def _numero_do_formulario(texto: str) -> float | None:
    """Número digitado ("0,300") ou None se vazio. Nome próprio: `_numero` é o teste dos cartões de clima."""
    texto = texto.strip().replace(",", ".")
    return float(texto) if texto else None


@app.get("/relatorios", response_class=HTMLResponse)
def tela_relatorios(request: Request):
    return templates.TemplateResponse(
        request, "relatorios.html", {**contexto_base(request, "/relatorios"), **_contexto_relatorios(_fazenda(request))}
    )


@app.get("/parcial/relatorios", response_class=HTMLResponse)
def parcial_relatorios(request: Request):
    return templates.TemplateResponse(request, "_relatorios.html", _contexto_relatorios(_fazenda(request)))


def _contexto_relatorios(fazenda_id: int | None = None) -> dict:
    return {
        "abertas": dados.ordens_a_concluir(fazenda_id),
        "relatorios": dados.listar_relatorios(fazenda_id),
    }


@app.get("/os/{os_id}/concluir", response_class=HTMLResponse)
def formulario_concluir(request: Request, os_id: int):
    os_ = dados.ordem(os_id)
    if os_ is None:
        raise HTTPException(404, "OS não encontrada ou já concluída")
    inicio = os_["inicio_previsto"].astimezone(FUSO)
    contexto = {
        "os": os_,
        "inicio": f"{inicio:%Y-%m-%dT%H:%M}",
        "fim": f"{inicio + timedelta(hours=float(os_['duracao_horas'])):%Y-%m-%dT%H:%M}",
    }
    return templates.TemplateResponse(request, "_concluir.html", contexto)


def _gerar_relatorio(os_id: int) -> tuple[str, str]:
    """(nível, texto) para a tela depois de pedir o PDF ao `agents`."""
    registro, falha = _agents("POST", f"/relatorios/os/{os_id}", timeout=60)
    if falha:
        return (
            "aviso",
            f"Execução da OS {os_id} gravada, mas o relatório não foi gerado: {falha} Gere de novo.",
        )
    veredito = {True: "dentro dos limites da bula", False: "FORA de um limite da bula"}.get(
        registro["conforme"], "conformidade não conferida"
    )
    return "ok", f"OS {os_id} concluída. Relatório v{registro['versao']} gerado: {veredito}."


@app.post("/os/{os_id}/concluir", response_class=HTMLResponse)
def concluir_os(
    request: Request,
    os_id: int,
    inicio_real: str = Form(...),
    fim_real: str = Form(...),
    area_aplicada_ha: str = Form(...),
    dose_real_por_ha: str = Form(""),
    volume_calda_l_ha: str = Form(""),
    modalidade: str = Form("terrestre"),
    receituario_numero: str = Form(""),
    responsavel_tecnico: str = Form(""),
    registro_profissional: str = Form(""),
    altura_voo_m: str = Form(""),
    largura_faixa_m: str = Form(""),
    aeronave: str = Form(""),
    ponta: str = Form(""),
    observacoes: str = Form(""),
):
    """Contrato 28: grava a execução, marca a OS como executada e pede o relatório (G7)."""
    inicio, fim = _inicio_local(inicio_real), _inicio_local(fim_real)
    try:
        numeros = {
            "area_aplicada_ha": _numero_do_formulario(area_aplicada_ha),
            "dose_real_por_ha": _numero_do_formulario(dose_real_por_ha),
            "volume_calda_l_ha": _numero_do_formulario(volume_calda_l_ha),
            "altura_voo_m": _numero_do_formulario(altura_voo_m),
            "largura_faixa_m": _numero_do_formulario(largura_faixa_m),
        }
    except ValueError:
        return _mensagem(request, "erro", "Use só números em área, dose, volume, altura e largura.")
    if inicio is None or fim is None or not numeros["area_aplicada_ha"] or numeros["area_aplicada_ha"] <= 0:
        return _mensagem(request, "erro", "Informe o início, o término e a área aplicada.")
    if fim > _agora():
        return _mensagem(request, "erro", "O término não pode estar no futuro.")
    textos = {
        "receituario_numero": receituario_numero,
        "responsavel_tecnico": responsavel_tecnico,
        "registro_profissional": registro_profissional,
        "aeronave": aeronave,
        "ponta": ponta,
        "observacoes": observacoes,
    }
    execucao = {
        "inicio_real": inicio,
        "fim_real": fim,
        "modalidade": modalidade,
        **{k: v for k, v in numeros.items() if v is None or v > 0},
        **{k: v.strip() or None for k, v in textos.items()},
    }
    r = escrita.concluir_os(os_id, execucao)
    if not r["ok"]:
        return _mensagem(request, "erro", r["erro"])
    nivel, texto = _gerar_relatorio(os_id)
    return _mensagem(request, nivel, texto, **{"HX-Trigger": "relatorios-mudou"})


@app.post("/relatorios/os/{os_id}/gerar", response_class=HTMLResponse)
def gerar_relatorio(request: Request, os_id: int):
    """Gera de novo (nova versão): quando a primeira geração falhou ou a bula foi lida depois."""
    registro, falha = _agents("POST", f"/relatorios/os/{os_id}", timeout=60)
    if falha:
        return _mensagem(request, "erro", f"Não consegui gerar o relatório: {falha}")
    texto = f"Relatório da OS {os_id} gerado (versão {registro['versao']})."
    return _mensagem(request, "ok", texto, **{"HX-Trigger": "relatorios-mudou"})


@app.get("/relatorios/{relatorio_id}/pdf")
def baixar_relatorio(relatorio_id: int) -> Response:
    relatorio = dados.pdf_do_relatorio(relatorio_id)
    if relatorio is None:
        raise HTTPException(404, "relatório não encontrado")
    nome = f"relatorio-aplicacao-os{relatorio['os_id']}-v{relatorio['versao']}.pdf"
    return Response(
        bytes(relatorio["pdf"]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{nome}"'},
    )
