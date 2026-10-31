"""Painel web do TerraMind: FastAPI + Jinja2 + HTMX servido localmente, sem login nem CDN.

    uv run uvicorn painel.main:app --port 8080

Telas: 1 ordens de serviço, 2 linha do tempo do incidente, 3 aprovação pendente, 4 simular chuva.
"""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from painel import dados

AQUI = Path(__file__).parent
FUSO = ZoneInfo("America/Sao_Paulo")  # contrato 12
RESPOSTAS = {"SIM": "SIM", "NAO": "NÃO", "OPCOES": "Ver opções"}
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


templates.env.filters.update(hora=_hora, hms=_hms)
templates.env.globals.update(STATUS_OS=STATUS_OS, STATUS_INCIDENTE=STATUS_INCIDENTE)


def cliente_http() -> httpx.Client:
    """Cliente para o `agents` e o Monitor (os testes trocam esta função)."""
    return httpx.Client(timeout=30)


def _url(variavel: str, padrao: str) -> str:
    return os.environ.get(variavel, padrao).rstrip("/")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/", include_in_schema=False)
def raiz() -> RedirectResponse:
    return RedirectResponse("/os")


# --- Tela 1: ordens de serviço ---------------------------------------------------------------
@app.get("/os", response_class=HTMLResponse)
def tela_os(request: Request):
    return templates.TemplateResponse(request, "os.html", {"ordens": dados.listar_ordens(), "ativa": "os"})


@app.get("/parcial/os", response_class=HTMLResponse)
def parcial_os(request: Request):
    """Só as `<tr>` da tabela: o HTMX troca o corpo da tabela a cada 2 s."""
    return templates.TemplateResponse(request, "_linhas_os.html", {"ordens": dados.listar_ordens()})


@app.get("/parcial/alertas", response_class=HTMLResponse)
def parcial_alertas(request: Request):
    """Faixa vermelha para incidentes em `aguardando_decisao_manual` (telas 1 e 3)."""
    return templates.TemplateResponse(
        request, "_alertas.html", {"manuais": dados.incidentes_decisao_manual()}
    )


# --- Tela 2: linha do tempo ------------------------------------------------------------------
def _contexto_timeline(incidente: int | None) -> dict:
    incidentes = dados.listar_incidentes()
    escolhido = incidente if incidente is not None else (incidentes[0]["id"] if incidentes else None)
    eventos = []
    for a in dados.auditoria_do_incidente(escolhido) if escolhido is not None else []:
        texto, citacoes = dados.separar_citacoes(a["justificativa"])
        mudancas = dados.resumo(a["antes"], a["depois"])
        eventos.append({**a, "texto": texto, "citacoes": citacoes, "mudancas": mudancas})
    return {"incidentes": incidentes, "escolhido": escolhido, "eventos": eventos}


@app.get("/timeline", response_class=HTMLResponse)
def tela_timeline(request: Request, incidente: int | None = None):
    return templates.TemplateResponse(
        request, "timeline.html", {**_contexto_timeline(incidente), "ativa": "timeline"}
    )


@app.get("/parcial/timeline", response_class=HTMLResponse)
def parcial_timeline(request: Request, incidente: int | None = None):
    return templates.TemplateResponse(request, "_eventos.html", _contexto_timeline(incidente))


# --- Tela 3: aprovação pendente --------------------------------------------------------------
@app.get("/aprovacao", response_class=HTMLResponse)
def tela_aprovacao(request: Request):
    return templates.TemplateResponse(
        request, "aprovacao.html", {"planos": dados.planos_pendentes(), "ativa": "aprovacao"}
    )


@app.get("/parcial/pendentes", response_class=HTMLResponse)
def parcial_pendentes(request: Request):
    return templates.TemplateResponse(request, "_pendentes.html", {"planos": dados.planos_pendentes()})


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
        nivel, texto = "erro", "Não consegui falar com o serviço de agentes. Tente de novo em instantes."
    else:
        if r.status_code == 409:
            nivel, texto = "aviso", "Decisão já registrada para este plano. Nada foi alterado."
        elif r.status_code == 404:
            nivel, texto = "erro", "Plano não encontrado."
        elif r.is_success:
            nivel, texto = "ok", f"Resposta registrada: {RESPOSTAS[resposta]}."
        else:
            nivel, texto = "erro", f"O serviço de agentes recusou a resposta (HTTP {r.status_code})."
    return templates.TemplateResponse(request, "_mensagem.html", {"nivel": nivel, "texto": texto})


# --- Tela 4: simular chuva -------------------------------------------------------------------
@app.get("/simular", response_class=HTMLResponse)
def tela_simular(request: Request):
    return templates.TemplateResponse(request, "simular.html", {"ativa": "simular"})


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
