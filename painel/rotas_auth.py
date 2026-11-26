"""Login, logout, conta e cadastro de usuários do painel (F3, contrato 23).

`proteger(app)` põe o porteiro na frente de toda rota: sem sessão válida, páginas vão para `/login` e
chamadas do HTMX ou da API recebem 401. Em requisição que muda dados (POST), o cabeçalho `Origin` (ou
`Referer`) tem de ser do próprio painel: junto do cookie `SameSite=Lax`, isso fecha o CSRF sem mexer nos
formulários. As rotas ficam direto no `app` (e não num `APIRouter`) pelo mesmo motivo do F1: o
`contexto_base` do E1 lê `app.routes` plano.
"""

import os
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from painel import auth, dados

COOKIE = "tm_sessao"
PUBLICAS_EXATAS = {"/login", "/health"}
PUBLICAS_PREFIXO = ("/static/",)
METODOS_SEGUROS = {"GET", "HEAD", "OPTIONS"}
AVISOS = {
    "criado": "Usuário criado.",
    "editado": "Usuário atualizado.",
    "desativado": "Usuário desativado.",
    "reativado": "Usuário reativado.",
}


def _cookie_seguro() -> bool:
    return os.environ.get("PAINEL_COOKIE_SEGURO", "").strip().lower() in {"1", "true", "sim"}


def caminho_local(destino: str) -> bool:
    partes = urlsplit(destino)
    return (
        destino.startswith("/")
        and not destino.startswith("//")
        and "\\" not in destino
        and not partes.scheme
        and not partes.netloc
    )


def origem_confere(request: Request) -> bool:
    """A requisição que muda dados veio de uma página do próprio painel (compara com o `Host`)."""
    origem = request.headers.get("origin") or request.headers.get("referer")
    if not origem:
        return False
    return urlsplit(origem).netloc == request.headers.get("host", "")


def _endereco(request: Request) -> str:
    return request.client.host if request.client else "?"


def proteger(app: FastAPI) -> None:
    @app.middleware("http")
    async def porteiro(request: Request, call_next):
        request.state.usuario = None
        if not auth.autenticacao_ligada():
            return await call_next(request)
        caminho = request.url.path
        publica = caminho in PUBLICAS_EXATAS or caminho.startswith(PUBLICAS_PREFIXO)
        token = request.cookies.get(COOKIE)
        if token:
            request.state.usuario = await run_in_threadpool(auth.usuario_da_sessao, token)
        if request.method not in METODOS_SEGUROS and not origem_confere(request):
            return Response("Requisição de origem desconhecida.", status_code=403)
        if request.state.usuario is None and not publica:
            if request.headers.get("hx-request") or caminho.startswith(("/api/", "/parcial/")):
                return Response("Entre para continuar.", status_code=401, headers={"HX-Redirect": "/login"})
            destino = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            vai_e_volta = request.method == "GET" and destino != "/"
            return RedirectResponse(
                f"/login?proximo={quote(destino, safe='')}" if vai_e_volta else "/login", status_code=303
            )
        return await call_next(request)


def _dar_cookie(resposta: Response, token: str) -> None:
    resposta.set_cookie(
        COOKIE,
        token,
        max_age=auth.DURACAO_SESSAO_H * 3600,
        httponly=True,
        samesite="lax",
        secure=_cookie_seguro(),
        path="/",
    )


async def formulario_multiplo(request: Request) -> dict[str, Any]:
    enviado = await request.form()
    return {k: (enviado.getlist(k) if k == "fazendas" else enviado.get(k)) for k in enviado}


Formulario = Annotated[dict[str, Any], Depends(formulario_multiplo)]


def registrar(app: FastAPI, templates, contexto_base: Callable[..., dict]) -> None:
    gerente = [Depends(auth.exigir("usuarios"))]

    def _logado(request: Request) -> dict[str, Any]:
        usuario = request.state.usuario
        if usuario is None:  # autenticação desligada (testes): não há quem faça a ação
            raise HTTPException(401, "Entre para continuar.")
        return usuario

    # --- entrar e sair ---------------------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def tela_login(request: Request, proximo: str = ""):
        if request.state.usuario is not None:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request, "login.html", {"proximo": proximo, "erro": None, "email": ""}
        )

    @app.post("/login", include_in_schema=False)
    def entrar(request: Request, form: Formulario):
        email, senha = (form.get("email") or "").strip(), form.get("senha") or ""
        proximo = form.get("proximo") or ""
        endereco = _endereco(request)

        def negar(mensagem: str, codigo: int):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"proximo": proximo, "erro": mensagem, "email": email},
                status_code=codigo,
            )

        if auth.bloqueado(email, endereco):
            return negar("Muitas tentativas. Aguarde alguns minutos e tente de novo.", 429)
        usuario = auth.autenticar(email, senha) if email and senha else None
        if usuario is None:
            auth.registrar_falha(email, endereco)
            return negar("E-mail ou senha incorretos.", 401)
        auth.limpar_falhas(email)
        resposta = RedirectResponse(proximo if caminho_local(proximo) else "/", status_code=303)
        _dar_cookie(resposta, auth.abrir_sessao(usuario["id"]))
        return resposta

    @app.post("/logout", include_in_schema=False)
    def sair(request: Request):
        token = request.cookies.get(COOKIE)
        if token:
            auth.encerrar_sessao(token)
        resposta = RedirectResponse("/login", status_code=303)
        resposta.delete_cookie(COOKIE, path="/")
        return resposta

    # --- a própria conta --------------------------------------------------------------------
    @app.get("/conta", response_class=HTMLResponse, include_in_schema=False)
    def tela_conta(request: Request, ok: str = ""):
        _logado(request)
        return templates.TemplateResponse(
            request, "conta.html", {**contexto_base(request, "/conta"), "erro": None, "ok": ok == "senha"}
        )

    @app.post("/conta/senha", include_in_schema=False)
    def mudar_senha(request: Request, form: Formulario):
        usuario = _logado(request)
        nova = form.get("nova") or ""
        if nova != (form.get("confirmacao") or ""):
            erro = "A confirmação não é igual à nova senha."
        else:
            erro = auth.trocar_senha(usuario["id"], form.get("atual") or "", nova)
        if erro:
            contexto = {**contexto_base(request, "/conta"), "erro": erro, "ok": False}
            return templates.TemplateResponse(request, "conta.html", contexto, status_code=422)
        resposta = RedirectResponse("/conta?ok=senha", status_code=303)
        _dar_cookie(resposta, auth.abrir_sessao(usuario["id"]))  # as sessões antigas foram encerradas
        return resposta

    # --- usuários (só o gerente) --------------------------------------------------------------
    def pagina_usuarios(request: Request, status: int = 200, **extra: Any) -> HTMLResponse:
        org = _logado(request)["organizacao_id"]
        contexto = {
            **contexto_base(request, "/usuarios"),
            "usuarios": auth.listar_usuarios(org),
            "fazendas_org": dados.listar_fazendas_da_organizacao(org),
            "operadores_org": [
                [str(o["id"]), f"{o['fazenda']} · {o['nome']}"]
                for o in dados.listar_operadores_da_organizacao(org)
            ],
            "form": None,
            "aviso": None,
            "erro_geral": None,
            **extra,
        }
        return templates.TemplateResponse(request, "usuarios.html", contexto, status_code=status)

    @app.get("/usuarios", response_class=HTMLResponse, dependencies=gerente, include_in_schema=False)
    def tela_usuarios(request: Request, novo: int = 0, editar: int | None = None, ok: str = ""):
        usuario = _logado(request)
        form = None
        if novo:
            form = {"id": None, "valores": {"papel": "operador", "fazendas": []}, "erros": {}}
        elif editar is not None:
            registro = auth.obter_usuario(editar, usuario["organizacao_id"])
            if registro is None:
                raise HTTPException(404, "Usuário não encontrado.")
            valores = {k: ("" if v is None else v) for k, v in registro.items()}
            form = {"id": editar, "valores": valores, "erros": {}}
        return pagina_usuarios(request, form=form, aviso=AVISOS.get(ok))

    def _salvar(request: Request, form: dict[str, Any], usuario_id: int | None):
        usuario = _logado(request)
        r = auth.salvar_usuario(form, usuario["organizacao_id"], usuario, usuario_id)
        if r.ok:
            return RedirectResponse(f"/usuarios?ok={'editado' if usuario_id else 'criado'}", status_code=303)
        escolhidas = [int(f) for f in form.get("fazendas") or [] if str(f).isdecimal()]
        valores = {**{k: v for k, v in form.items() if k != "senha"}, "fazendas": escolhidas}
        return pagina_usuarios(request, 422, form={"id": usuario_id, "valores": valores, "erros": r.erros})

    @app.post("/usuarios", dependencies=gerente, include_in_schema=False)
    def criar_usuario(request: Request, form: Formulario):
        return _salvar(request, form, None)

    @app.post("/usuarios/{usuario_id}", dependencies=gerente, include_in_schema=False)
    def editar_usuario(request: Request, usuario_id: int, form: Formulario):
        return _salvar(request, form, usuario_id)

    def _alternar(request: Request, usuario_id: int, ativo: bool):
        usuario = _logado(request)
        motivo = auth.alternar_usuario(usuario_id, ativo, usuario["organizacao_id"], usuario)
        if motivo:
            return pagina_usuarios(request, 422, erro_geral=motivo)
        return RedirectResponse(f"/usuarios?ok={'reativado' if ativo else 'desativado'}", status_code=303)

    @app.post("/usuarios/{usuario_id}/desativar", dependencies=gerente, include_in_schema=False)
    def desativar_usuario(request: Request, usuario_id: int):
        return _alternar(request, usuario_id, False)

    @app.post("/usuarios/{usuario_id}/reativar", dependencies=gerente, include_in_schema=False)
    def reativar_usuario(request: Request, usuario_id: int):
        return _alternar(request, usuario_id, True)
