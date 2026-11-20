"""Painel web do TerraMind: FastAPI + Jinja2 + HTMX servido localmente, sem login nem CDN.

    uv run uvicorn painel.main:app --port 8080

Telas: 1 ordens de serviço, 2 linha do tempo do incidente, 3 aprovação pendente, 4 simular chuva,
Pergunte à bula (G4, `/bula`), perda evitada (G1, `/perda`), agenda e janela da OS (G2 e G3, `/agenda`)
e relatórios de aplicação (G7, `/relatorios`).
"""

import os
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from painel import dados, escrita

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


DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
templates.env.filters.update(hora=_hora, hms=_hms, dia=_dia)
templates.env.globals.update(STATUS_OS=STATUS_OS, STATUS_INCIDENTE=STATUS_INCIDENTE)

# --- Fazenda escolhida (F2, contrato 20) ------------------------------------------------------
# Fica num cookie: as telas atualizadas pelo HTMX (a cada 2 s) o mandam sozinhas. Sem cookie, todas as
# fazendas. Quando o F3 trouxer o login, o seletor passa a listar só as fazendas do usuário.
COOKIE_FAZENDA = "fazenda"


def _fazenda(request: Request) -> int | None:
    valor = request.cookies.get(COOKIE_FAZENDA, "")
    return int(valor) if valor.isdecimal() else None


def _fazendas_do_seletor() -> list[dict]:
    return dados.listar_fazendas()


templates.env.globals["fazendas"] = _fazendas_do_seletor


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


@app.get("/fazenda/{fazenda_id}", include_in_schema=False)
def escolher_fazenda(fazenda_id: int, volta: str = "/os") -> RedirectResponse:
    """Guarda a fazenda escolhida (0 = todas) e volta para a tela de onde veio."""
    destino = volta if volta.startswith("/") and not volta.startswith("//") else "/os"
    resposta = RedirectResponse(destino, status_code=303)
    if fazenda_id > 0:
        resposta.set_cookie(COOKIE_FAZENDA, str(fazenda_id), max_age=60 * 60 * 24 * 365, samesite="lax")
    else:
        resposta.delete_cookie(COOKIE_FAZENDA)
    return resposta


# --- Tela 1: ordens de serviço ---------------------------------------------------------------
@app.get("/os", response_class=HTMLResponse)
def tela_os(request: Request):
    return templates.TemplateResponse(request, "os.html", {"ordens": dados.listar_ordens(_fazenda(request))})


@app.get("/parcial/os", response_class=HTMLResponse)
def parcial_os(request: Request):
    """Só as `<tr>` da tabela: o HTMX troca o corpo da tabela a cada 2 s."""
    return templates.TemplateResponse(
        request, "_linhas_os.html", {"ordens": dados.listar_ordens(_fazenda(request))}
    )


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


@app.get("/timeline", response_class=HTMLResponse)
def tela_timeline(request: Request, incidente: int | None = None):
    contexto = _contexto_timeline(incidente, _fazenda(request))
    return templates.TemplateResponse(request, "timeline.html", contexto)


@app.get("/parcial/timeline", response_class=HTMLResponse)
def parcial_timeline(request: Request, incidente: int | None = None):
    contexto = _contexto_timeline(incidente, _fazenda(request))
    return templates.TemplateResponse(request, "_eventos.html", contexto)


# --- Tela 3: aprovação pendente --------------------------------------------------------------
@app.get("/aprovacao", response_class=HTMLResponse)
def tela_aprovacao(request: Request):
    return templates.TemplateResponse(request, "aprovacao.html")


@app.get("/parcial/pendentes", response_class=HTMLResponse)
def parcial_pendentes(request: Request):
    agora = _agora()
    planos = [
        {**p, "atraso": dados.linhas_atraso(p["custos_atraso"], agora)}
        for p in dados.planos_pendentes(_fazenda(request))
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
    produtos = dados.listar_produtos(_fazenda(request))
    return templates.TemplateResponse(request, "bula.html", {"produtos": produtos})


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


# --- Tela 4: simular chuva -------------------------------------------------------------------
@app.get("/simular", response_class=HTMLResponse)
def tela_simular(request: Request):
    return templates.TemplateResponse(request, "simular.html")


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
@app.get("/perda", response_class=HTMLResponse)
def tela_perda(request: Request, inicio: date | None = None, fim: date | None = None, replay: bool = False):
    """Relatório de fim de safra: tudo vem de `agente.impactos`; incidente de replay só com `replay=1`."""
    hoje = _agora().date()
    inicio = inicio or dados.inicio_da_safra(hoje)
    fim = fim or hoje
    contexto = {"p": dados.perda_evitada(inicio, fim, replay, _fazenda(request)), "replay": replay}
    return templates.TemplateResponse(request, "perda.html", contexto)


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
    fazenda = _fazenda(request)
    contexto = {
        "insumos": dados.listar_insumos(fazenda),
        "talhoes": dados.listar_talhoes(fazenda),
        "operadores": dados.listar_operadores(fazenda),
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
def _numero(texto: str) -> float | None:
    texto = texto.strip().replace(",", ".")
    return float(texto) if texto else None


@app.get("/relatorios", response_class=HTMLResponse)
def tela_relatorios(request: Request):
    return templates.TemplateResponse(request, "relatorios.html", _contexto_relatorios(_fazenda(request)))


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
            "area_aplicada_ha": _numero(area_aplicada_ha),
            "dose_real_por_ha": _numero(dose_real_por_ha),
            "volume_calda_l_ha": _numero(volume_calda_l_ha),
            "altura_voo_m": _numero(altura_voo_m),
            "largura_faixa_m": _numero(largura_faixa_m),
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
