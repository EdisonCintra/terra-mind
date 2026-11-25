"""Telas de cadastro (F1): `/cadastros/<aba>` e o proxy da busca de produto (E5).

Cada aba é uma lista com o formulário na mesma página (`?novo=1` ou `?editar=<id>`). Os formulários são
POST comuns com redirecionamento (funcionam sem JavaScript); o JavaScript só entra no desenho do talhão
no mapa, na conferência da janela da OS (G3) e na busca de produto. Erro de validação refaz a página com a
mensagem em cada campo (HTTP 422).

Quem pode cadastrar é o gerente (contrato 23): com usuário logado e papel diferente, 403. Sem login ainda
(F3), a rota fica aberta como o resto do painel.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from painel import cadastros, dados, escrita
from painel.cadastros import ENTIDADES

FUSO = ZoneInfo("America/Sao_Paulo")
ABAS = [(e.slug, e.titulo) for e in ENTIDADES.values()]
ABAS.insert(4, ("os", "Ordens de serviço"))  # talhões, máquinas, operadores, insumos, OS, safra
AVISOS = {
    "criado": "{} criado.",
    "alterado": "{} alterado.",
    "desativado": "{} desativado. O histórico continua guardado.",
    "reativado": "{} reativado.",
}
REGISTRO = re.compile(r"^[0-9A-Za-z._-]{1,40}$")
LIMITE_PDF = 20 * 1024 * 1024
CLASSES = (("fungicida", "fungicida"), ("herbicida", "herbicida"), ("inseticida", "inseticida"))


def tipo_do_insumo(classe: str | None) -> str | None:
    """A classe do Agrofit ("Fungicida", "Inseticida/Acaricida") no `erp.insumos.tipo`; `None` se for de
    fora do escopo (só fungicida, herbicida e inseticida, contrato 15)."""
    texto = (classe or "").lower()
    return next((tipo for tipo, palavra in CLASSES if palavra in texto), None)


def _inicio_local(texto: str) -> datetime | None:
    try:
        return datetime.fromisoformat(texto).replace(tzinfo=FUSO)
    except ValueError:
        return None


def valores_da_os(o: dict[str, Any]) -> dict[str, str]:
    """A OS como texto de formulário (para editar)."""
    return {
        "talhao_id": str(o["talhao_id"]),
        "insumo_id": str(o["insumo_id"] or ""),
        "dose_por_ha": "" if o["dose_por_ha"] is None else f"{float(o['dose_por_ha']):g}",
        "inicio": o["inicio_previsto"].astimezone(FUSO).strftime("%Y-%m-%dT%H:%M"),
        "duracao_horas": f"{float(o['duracao_horas']):g}",
        "operador_id": str(o["operador_id"] or ""),
        "maquina_id": str(o["maquina_id"] or ""),
    }


def _numero(texto: str | None) -> float | None:
    try:
        return float((texto or "").strip().replace(",", "."))
    except ValueError:
        return None


async def formulario(request: Request) -> dict[str, str]:
    return {k: v for k, v in (await request.form()).items() if isinstance(v, str)}


def exigir_gerente(request: Request) -> None:
    """Contrato 23: só o gerente cadastra. Sem usuário na requisição (antes do login do F3), passa."""
    usuario = getattr(request.state, "usuario", None)
    if usuario and usuario.get("papel") not in (None, "gerente"):
        raise HTTPException(403, "Só o gerente cadastra.")


Formulario = Annotated[dict[str, str], Depends(formulario)]


def registrar(
    app: FastAPI,
    templates,
    contexto_base: Callable[..., dict],
    fazenda_do_request: Callable[[Request], int | None],
    http: Callable[[], httpx.Client],
    url_conhecimento: Callable[[], str],
) -> None:
    """Registra as rotas no `app` (e não num `APIRouter`: o `contexto_base` do E1 lê `app.routes` plano)."""
    router = app

    gerente = [Depends(exigir_gerente)]

    def contexto(request: Request, aba: str, **extra: Any) -> dict[str, Any]:
        return {**contexto_base(request, "/cadastros"), "aba": aba, "abas": ABAS, "avisos": AVISOS, **extra}

    def aviso(ok: str | None, singular: str) -> str | None:
        return AVISOS[ok].format(singular) if ok in AVISOS else None

    def pagina(request: Request, aba: str, status: int = 200, **extra: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request, "cadastros.html", contexto(request, aba, **extra), status_code=status
        )

    def redirecionar(aba: str, ok: str) -> RedirectResponse:
        return RedirectResponse(f"/cadastros/{aba}?ok={ok}", status_code=303)

    # --- abas genéricas (talhões, máquinas, operadores, insumos, safra) ------------------------
    def tela_entidade(
        request: Request, ent: cadastros.Entidade, novo: bool, editar: int | None, ok: str | None
    ):
        fazenda = fazenda_do_request(request)
        form = None
        if novo:
            form = {
                "valores": {c.nome: c.padrao for c in ent.campos},
                "erros": {},
                "id": None,
                "erro_geral": None,
            }
        elif editar is not None:
            registro = cadastros.obter(ent, editar, fazenda)
            if registro is None:
                raise HTTPException(404, f"{ent.singular} não encontrado.")
            form = {
                "valores": cadastros.valores_do_formulario(ent, registro),
                "erros": {},
                "id": editar,
                "erro_geral": None,
            }
        return pagina(
            request,
            ent.slug,
            ent=ent,
            linhas=cadastros.listar(ent, fazenda),
            form=form,
            opcoes=cadastros.opcoes_dinamicas(ent, fazenda),
            flash=aviso(ok, ent.singular),
        )

    def tela_os(request: Request, novo: bool, editar: int | None, ok: str | None):
        fazenda = fazenda_do_request(request)
        ordens = cadastros.ordens_da_fazenda(fazenda)
        form = None
        if novo:
            form = {"id": None, "valores": {}, "erro_geral": None}
        elif editar is not None:
            atual = next((o for o in ordens if o["id"] == editar), None)
            if atual is None:
                raise HTTPException(404, "OS não encontrada.")
            form = {"id": editar, "os": atual, "valores": valores_da_os(atual), "erro_geral": None}
        return pagina(
            request,
            "os",
            linhas=ordens,
            form=form,
            talhoes=dados.listar_talhoes(fazenda),
            insumos=dados.listar_insumos(fazenda),
            operadores=dados.listar_operadores(fazenda),
            editaveis=escrita.STATUS_EDITAVEIS,
            flash={"criado": "OS criada.", "alterado": "OS alterada.", "cancelada": "OS cancelada."}.get(
                ok or ""
            ),
        )

    @router.get("/cadastros", response_class=HTMLResponse, dependencies=gerente)
    @router.get("/cadastros/{aba}", response_class=HTMLResponse, dependencies=gerente)
    def ver(
        request: Request,
        aba: str = "talhoes",
        novo: bool = False,
        editar: int | None = None,
        ok: str | None = None,
    ):
        if aba == "os":
            return tela_os(request, novo, editar, ok)
        if aba not in ENTIDADES:
            raise HTTPException(404, "Aba não encontrada.")
        return tela_entidade(request, ENTIDADES[aba], novo, editar, ok)

    # --- escrita das abas genéricas ---------------------------------------------------------------
    def salvar(request: Request, ent: cadastros.Entidade, form: dict[str, str], registro_id: int | None):
        resultado = cadastros.salvar(ent, form, fazenda_do_request(request), registro_id)
        if resultado.ok:
            return redirecionar(ent.slug, "criado" if registro_id is None else "alterado")
        fazenda = fazenda_do_request(request)
        return pagina(
            request,
            ent.slug,
            422,
            ent=ent,
            linhas=cadastros.listar(ent, fazenda),
            form={
                "valores": resultado.valores,
                "erros": resultado.erros,
                "id": registro_id,
                "erro_geral": resultado.erro_geral,
            },
            opcoes=cadastros.opcoes_dinamicas(ent, fazenda),
            flash=None,
        )

    @router.post("/cadastros/os", dependencies=gerente)
    def criar_os(request: Request, form: Formulario):
        return gravar_os(request, form, None)

    @router.post("/cadastros/os/{os_id}", dependencies=gerente)
    def editar_os(request: Request, os_id: int, form: Formulario):
        return gravar_os(request, form, os_id)

    @router.post("/cadastros/os/{os_id}/cancelar", dependencies=gerente)
    def cancelar_os(request: Request, os_id: int):
        fazenda = fazenda_do_request(request)
        ordens = cadastros.ordens_da_fazenda(fazenda)
        if not any(o["id"] == os_id for o in ordens):
            raise HTTPException(404, "OS não encontrada.")
        resultado = escrita.cancelar_os(os_id)
        if resultado["ok"]:
            return redirecionar("os", "cancelada")
        return pagina_os_com_erro(request, resultado["erro"], None, {})

    def pagina_os_com_erro(request: Request, erro: str, os_id: int | None, valores: dict[str, str]):
        fazenda = fazenda_do_request(request)
        ordens = cadastros.ordens_da_fazenda(fazenda)
        atual = next((o for o in ordens if o["id"] == os_id), None) if os_id is not None else None
        return pagina(
            request,
            "os",
            422,
            linhas=ordens,
            form={"id": os_id, "os": atual, "valores": valores, "erro_geral": erro},
            talhoes=dados.listar_talhoes(fazenda),
            insumos=dados.listar_insumos(fazenda),
            operadores=dados.listar_operadores(fazenda),
            editaveis=escrita.STATUS_EDITAVEIS,
            flash=None,
        )

    def gravar_os(request: Request, form: dict[str, str], os_id: int | None):
        fazenda = fazenda_do_request(request)
        inicio = _inicio_local(form.get("inicio", ""))
        dose, duracao = _numero(form.get("dose_por_ha")), _numero(form.get("duracao_horas"))
        maquina = form.get("maquina_id", "")
        operador = form.get("operador_id", "")
        if inicio is None or dose is None or dose <= 0 or duracao is None or duracao <= 0:
            return pagina_os_com_erro(request, "Confira a data de início, a dose e a duração.", os_id, form)
        if not maquina.isdecimal() or (operador and not operador.isdecimal()):
            return pagina_os_com_erro(
                request, "Escolha o pulverizador na conferência da janela.", os_id, form
            )
        operador_id = int(operador) if operador else None
        if os_id is None:
            talhao, insumo = form.get("talhao_id", ""), form.get("insumo_id", "")
            if not talhao.isdecimal() or not insumo.isdecimal():
                return pagina_os_com_erro(request, "Escolha o talhão e o produto.", None, form)
            if int(talhao) not in {t["id"] for t in dados.listar_talhoes(fazenda)}:
                return pagina_os_com_erro(request, "Escolha um talhão ativo desta fazenda.", None, form)
            resultado = escrita.criar_os(
                int(talhao), int(insumo), dose, inicio, duracao, int(maquina), operador_id
            )
        else:
            if not any(o["id"] == os_id for o in cadastros.ordens_da_fazenda(fazenda)):
                raise HTTPException(404, "OS não encontrada.")
            resultado = escrita.editar_os(os_id, dose, inicio, duracao, int(maquina), operador_id)
        if resultado["ok"]:
            return redirecionar("os", "criado" if os_id is None else "alterado")
        return pagina_os_com_erro(request, resultado["erro"], os_id, form)

    @router.post("/cadastros/{aba}", dependencies=gerente)
    def criar(request: Request, aba: str, form: Formulario):
        if aba not in ENTIDADES:
            raise HTTPException(404, "Aba não encontrada.")
        return salvar(request, ENTIDADES[aba], form, None)

    @router.post("/cadastros/{aba}/{registro_id}", dependencies=gerente)
    def editar(request: Request, aba: str, registro_id: int, form: Formulario):
        if aba not in ENTIDADES:
            raise HTTPException(404, "Aba não encontrada.")
        return salvar(request, ENTIDADES[aba], form, registro_id)

    def alternar(request: Request, aba: str, registro_id: int, ativo: bool):
        if aba not in ENTIDADES:
            raise HTTPException(404, "Aba não encontrada.")
        ent = ENTIDADES[aba]
        fazenda = fazenda_do_request(request)
        resultado = cadastros.alternar(ent, registro_id, ativo, fazenda)
        if resultado.ok:
            return redirecionar(aba, "reativado" if ativo else "desativado")
        return pagina(
            request,
            aba,
            422,
            ent=ent,
            linhas=cadastros.listar(ent, fazenda),
            form=None,
            opcoes=cadastros.opcoes_dinamicas(ent, fazenda),
            flash=None,
            erro_acao=resultado.erro_geral,
        )

    @router.post("/cadastros/{aba}/{registro_id}/desativar", dependencies=gerente)
    def desativar(request: Request, aba: str, registro_id: int):
        return alternar(request, aba, registro_id, False)

    @router.post("/cadastros/{aba}/{registro_id}/reativar", dependencies=gerente)
    def reativar(request: Request, aba: str, registro_id: int):
        return alternar(request, aba, registro_id, True)

    # --- busca de produto no Agrofit (E5) para o cadastro de insumo -------------------------------
    def repassar(metodo: str, caminho: str, **kwargs: Any) -> JSONResponse:
        url = f"{url_conhecimento()}{caminho}"
        try:
            with http() as cliente:
                r = cliente.request(metodo, url, timeout=60, **kwargs)
        except httpx.HTTPError:
            return JSONResponse({"detail": "A base de conhecimento está fora do ar."}, status_code=502)
        try:
            corpo = r.json()
        except ValueError:
            corpo = {"detail": f"Resposta inesperada da base de conhecimento (HTTP {r.status_code})."}
        return JSONResponse(corpo, status_code=r.status_code)

    @router.get("/api/produtos/busca", dependencies=gerente)
    def buscar_produto(q: str = ""):
        if len(q.strip()) < 3:
            return JSONResponse({"detail": "Digite pelo menos 3 letras."}, status_code=422)
        resposta = repassar("GET", "/produtos/busca", params={"q": q.strip()})
        if resposta.status_code != 200:
            return resposta
        produtos = json.loads(resposta.body)
        for p in produtos:
            p["tipo_insumo"] = tipo_do_insumo(p.get("classe"))
        return JSONResponse(produtos)

    @router.post("/api/produtos/{registro_mapa}/bula", dependencies=gerente)
    def enviar_bula(registro_mapa: str, arquivo: UploadFile):
        if not REGISTRO.match(registro_mapa):
            raise HTTPException(422, "Registro inválido.")
        conteudo = arquivo.file.read(LIMITE_PDF + 1)
        if len(conteudo) > LIMITE_PDF:
            return JSONResponse({"detail": "O PDF passa de 20 MB."}, status_code=413)
        return repassar(
            "POST",
            f"/produtos/{registro_mapa}/bula",
            files={"arquivo": (arquivo.filename or "bula.pdf", conteudo, "application/pdf")},
        )

