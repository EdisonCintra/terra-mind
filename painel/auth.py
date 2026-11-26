"""Usuários, senhas e sessões do painel (F3, contrato 23).

- Senha: só o hash, com `scrypt` da biblioteca padrão (sem dependência nova), sal aleatório por senha e os
  parâmetros dentro do próprio texto do hash (dá para endurecer depois sem quebrar as senhas antigas).
- Sessão: token aleatório de 256 bits no cookie; o banco guarda só o SHA-256 dele (`erp.sessoes`). Desativar o
  usuário ou trocar a senha apaga as sessões na hora, o que um cookie assinado sem estado não permite.
- Tentativas de login: limite por e-mail e por endereço, em memória (uma instância do painel; reiniciar zera).
- Papéis (contrato 23): `gerente` e `operador`. Quem pode o quê está em `PERMISSOES`, num lugar só.
"""

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import psycopg

from erp_mcp.db import conectar, registrar_auditoria

PAPEIS = ("gerente", "operador")
DURACAO_SESSAO_H = 12
SENHA_MIN, SENHA_MAX = 8, 128

# Tabela de permissões confirmada pelo time (plano F3). `operador` não cadastra nem aprova.
PERMISSOES: dict[str, frozenset[str]] = {
    "gerente": frozenset({"ver", "cadastrar", "usuarios", "os", "aprovar", "executar"}),
    "operador": frozenset({"ver", "os", "executar"}),
}


def pode(papel: str | None, acao: str) -> bool:
    return acao in PERMISSOES.get(papel or "", frozenset())


# --- senha ------------------------------------------------------------------------------------
_N, _R, _P = 2**15, 8, 1


def _scrypt(senha: str, sal: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(senha.encode(), salt=sal, n=n, r=r, p=p, maxmem=128 * 1024 * 1024, dklen=32)


def hash_da_senha(senha: str) -> str:
    sal = os.urandom(16)
    chave = _scrypt(senha, sal, _N, _R, _P)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(sal)}${b64(chave)}"


def senha_confere(senha: str, guardado: str) -> bool:
    try:
        esquema, n, r, p, sal, chave = guardado.split("$")
        if esquema != "scrypt":
            return False
        esperado = base64.b64decode(chave)
        obtido = _scrypt(senha, base64.b64decode(sal), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(obtido, esperado)


_HASH_FALSO = hash_da_senha(secrets.token_hex(8))  # gasta o mesmo tempo quando o e-mail não existe


def erro_de_senha(senha: str) -> str | None:
    if len(senha) < SENHA_MIN:
        return f"A senha precisa de pelo menos {SENHA_MIN} caracteres."
    if len(senha) > SENHA_MAX:
        return f"A senha pode ter no máximo {SENHA_MAX} caracteres."
    if senha.strip() != senha or senha.isdigit():
        return "Use uma senha sem espaços nas pontas e com letras, não só números."
    return None


# --- limite de tentativas ---------------------------------------------------------------------
JANELA_S, MAX_POR_EMAIL, MAX_POR_ENDERECO = 15 * 60, 5, 20
_falhas: dict[str, deque[float]] = defaultdict(deque)
_trava = threading.Lock()


def _recentes(chave: str, agora: float) -> deque[float]:
    fila = _falhas[chave]
    while fila and agora - fila[0] > JANELA_S:
        fila.popleft()
    return fila


def bloqueado(email: str, endereco: str, agora: float | None = None) -> bool:
    agora = time.monotonic() if agora is None else agora
    with _trava:
        return (
            len(_recentes(f"e:{email.lower()}", agora)) >= MAX_POR_EMAIL
            or len(_recentes(f"i:{endereco}", agora)) >= MAX_POR_ENDERECO
        )


def registrar_falha(email: str, endereco: str, agora: float | None = None) -> None:
    agora = time.monotonic() if agora is None else agora
    with _trava:
        _recentes(f"e:{email.lower()}", agora).append(agora)
        _recentes(f"i:{endereco}", agora).append(agora)


def limpar_falhas(email: str) -> None:
    with _trava:
        _falhas.pop(f"e:{email.lower()}", None)


def zerar_limites() -> None:  # testes
    with _trava:
        _falhas.clear()


# --- usuários e sessões -----------------------------------------------------------------------
def _hash_do_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _usuario_completo(conn: psycopg.Connection, linha: dict[str, Any]) -> dict[str, Any]:
    fazendas = conn.execute(
        "SELECT uf.fazenda_id FROM erp.usuario_fazendas uf JOIN erp.fazendas f ON f.id = uf.fazenda_id"
        " WHERE uf.usuario_id = %s AND f.organizacao_id = %s ORDER BY uf.fazenda_id",
        (linha["id"], linha["organizacao_id"]),
    ).fetchall()
    return {
        "id": linha["id"],
        "nome": linha["nome"],
        "email": linha["email"],
        "papel": linha["papel"],
        "organizacao_id": linha["organizacao_id"],
        "operador_id": linha["operador_id"],
        "fazendas": [f["fazenda_id"] for f in fazendas],
    }


def autenticar(email: str, senha: str) -> dict[str, Any] | None:
    """O usuário se o e-mail e a senha conferem e ele está ativo. Gasta o mesmo tempo nos dois casos."""
    with conectar() as conn:
        linha = conn.execute(
            "SELECT * FROM erp.usuarios WHERE lower(email) = lower(%s)", (email.strip(),)
        ).fetchone()
        confere = senha_confere(senha, linha["senha_hash"] if linha else _HASH_FALSO)
        if not linha or not confere or not linha["ativo"]:
            return None
        return _usuario_completo(conn, linha)


def abrir_sessao(usuario_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with conectar() as conn:
        conn.execute("DELETE FROM erp.sessoes WHERE expira_em < now()")
        conn.execute(
            "INSERT INTO erp.sessoes (token_hash, usuario_id, expira_em)"
            " VALUES (%s, %s, now() + make_interval(hours => %s))",
            (_hash_do_token(token), usuario_id, DURACAO_SESSAO_H),
        )
    return token


def encerrar_sessao(token: str) -> None:
    with conectar() as conn:
        conn.execute("DELETE FROM erp.sessoes WHERE token_hash = %s", (_hash_do_token(token),))


def usuario_da_sessao(token: str | None) -> dict[str, Any] | None:
    """O usuário dono do token, se a sessão não expirou e o usuário continua ativo."""
    if not token:
        return None
    try:
        with conectar() as conn:
            linha = conn.execute(
                "SELECT u.* FROM erp.sessoes s JOIN erp.usuarios u ON u.id = s.usuario_id"
                " WHERE s.token_hash = %s AND s.expira_em > now() AND u.ativo",
                (_hash_do_token(token),),
            ).fetchone()
            return _usuario_completo(conn, linha) if linha else None
    except psycopg.errors.UndefinedTable:  # banco sem a migration 007: ninguém entra
        return None


@dataclass
class Resultado:
    ok: bool
    erros: dict[str, str]
    id: int | None = None


def _fazendas_da_organizacao(conn: psycopg.Connection, organizacao_id: int) -> set[int]:
    linhas = conn.execute(
        "SELECT id FROM erp.fazendas WHERE organizacao_id = %s", (organizacao_id,)
    ).fetchall()
    return {linha["id"] for linha in linhas}


def listar_usuarios(organizacao_id: int) -> list[dict[str, Any]]:
    with conectar() as conn:
        return conn.execute(
            "SELECT u.id, u.nome, u.email, u.papel, u.ativo, u.telegram_chat_id, u.whatsapp_telefone,"
            " u.operador_id, o.nome AS operador,"
            " coalesce((SELECT string_agg(f.nome, ', ' ORDER BY f.nome) FROM erp.usuario_fazendas uf"
            "           JOIN erp.fazendas f ON f.id = uf.fazenda_id"
            "           WHERE uf.usuario_id = u.id), '') AS fazendas"
            " FROM erp.usuarios u LEFT JOIN erp.operadores o ON o.id = u.operador_id"
            " WHERE u.organizacao_id = %s ORDER BY u.ativo DESC, u.nome",
            (organizacao_id,),
        ).fetchall()


def obter_usuario(usuario_id: int, organizacao_id: int) -> dict[str, Any] | None:
    with conectar() as conn:
        linha = conn.execute(
            "SELECT id, nome, email, papel, ativo, telegram_chat_id, whatsapp_telefone, operador_id"
            " FROM erp.usuarios WHERE id = %s AND organizacao_id = %s",
            (usuario_id, organizacao_id),
        ).fetchone()
        if not linha:
            return None
        linha["fazendas"] = [
            f["fazenda_id"]
            for f in conn.execute(
                "SELECT fazenda_id FROM erp.usuario_fazendas WHERE usuario_id = %s ORDER BY fazenda_id",
                (usuario_id,),
            ).fetchall()
        ]
        return linha


def _inteiro(texto: str | None) -> int | None:
    texto = (texto or "").strip()
    if not texto:
        return None
    return int(texto.replace(" ", "").replace("-", "").replace("+", "").replace("(", "").replace(")", ""))


def _sincronizar_gerente(conn: psycopg.Connection, usuario_id: int) -> None:
    """Mantém `erp.gerentes` (lido pelos canais) igual ao usuário: uma linha por fazenda, só gerente ativo."""
    conn.execute("DELETE FROM erp.gerentes WHERE usuario_id = %s", (usuario_id,))
    conn.execute(
        "INSERT INTO erp.gerentes (fazenda_id, nome, telegram_chat_id, whatsapp_telefone, ativo, usuario_id)"
        " SELECT uf.fazenda_id, u.nome, u.telegram_chat_id, u.whatsapp_telefone, true, u.id"
        " FROM erp.usuarios u JOIN erp.usuario_fazendas uf ON uf.usuario_id = u.id"
        " WHERE u.id = %s AND u.papel = 'gerente' AND u.ativo",
        (usuario_id,),
    )


def _visivel(linha: dict[str, Any] | None) -> dict[str, Any] | None:
    """O que vai para a auditoria: nunca o hash da senha."""
    if linha is None:
        return None
    return {k: v for k, v in linha.items() if k != "senha_hash"}


def salvar_usuario(
    form: dict[str, Any],
    organizacao_id: int,
    por: dict[str, Any],
    usuario_id: int | None = None,
) -> Resultado:
    """Cria ou edita um usuário da organização. `form`: nome, email, papel, senha, fazendas (lista de ids),
    telegram_chat_id, whatsapp_telefone, operador_id. Senha vazia na edição mantém a atual."""
    erros: dict[str, str] = {}
    nome = (form.get("nome") or "").strip()
    email = (form.get("email") or "").strip()
    papel = form.get("papel") or ""
    senha = form.get("senha") or ""
    if not nome:
        erros["nome"] = "Informe o nome."
    elif len(nome) > 120:
        erros["nome"] = "Nome longo demais."
    if "@" not in email or " " in email or len(email) > 200 or email.startswith("@") or email.endswith("@"):
        erros["email"] = "Informe um e-mail válido."
    if papel not in PAPEIS:
        erros["papel"] = "Escolha gerente ou operador."
    if usuario_id is None or senha:
        if (msg := erro_de_senha(senha)) is not None:
            erros["senha"] = msg
    contatos: dict[str, int | None] = {}
    for campo in ("telegram_chat_id", "whatsapp_telefone", "operador_id"):
        try:
            contatos[campo] = _inteiro(form.get(campo))
        except ValueError:
            erros[campo] = "Use só números."
    try:
        fazendas = {int(f) for f in form.get("fazendas") or []}
    except (TypeError, ValueError):
        fazendas = set()
        erros["fazendas"] = "Fazenda inválida."
    if not fazendas and "fazendas" not in erros:
        erros["fazendas"] = "Escolha pelo menos uma fazenda."
    if erros:
        return Resultado(False, erros)

    try:
        with conectar() as conn:
            if not fazendas <= _fazendas_da_organizacao(conn, organizacao_id):
                return Resultado(False, {"fazendas": "Fazenda de outra organização."})
            operador_id = contatos["operador_id"]
            if operador_id is not None:
                ok = conn.execute(
                    "SELECT 1 FROM erp.operadores WHERE id = %s AND fazenda_id = ANY(%s)",
                    (operador_id, list(fazendas)),
                ).fetchone()
                if not ok:
                    return Resultado(False, {"operador_id": "Operador inexistente nas fazendas escolhidas."})
            antes = None
            if usuario_id is not None:
                antes = conn.execute(
                    "SELECT * FROM erp.usuarios WHERE id = %s AND organizacao_id = %s FOR UPDATE",
                    (usuario_id, organizacao_id),
                ).fetchone()
                if not antes:
                    return Resultado(False, {"nome": "Usuário não encontrado."})
                if usuario_id == por["id"] and papel != antes["papel"]:
                    return Resultado(False, {"papel": "Você não pode mudar o seu próprio papel."})
                conn.execute(
                    "UPDATE erp.usuarios SET nome = %s, email = %s, papel = %s, telegram_chat_id = %s,"
                    " whatsapp_telefone = %s, operador_id = %s WHERE id = %s",
                    (
                        nome,
                        email,
                        papel,
                        contatos["telegram_chat_id"],
                        contatos["whatsapp_telefone"],
                        operador_id,
                        usuario_id,
                    ),
                )
                if senha:
                    conn.execute(
                        "UPDATE erp.usuarios SET senha_hash = %s WHERE id = %s",
                        (hash_da_senha(senha), usuario_id),
                    )
                    conn.execute("DELETE FROM erp.sessoes WHERE usuario_id = %s", (usuario_id,))
                acao = "usuario_editar"
            else:
                usuario_id = conn.execute(
                    "INSERT INTO erp.usuarios (organizacao_id, nome, email, senha_hash, papel,"
                    " telegram_chat_id,"
                    " whatsapp_telefone, operador_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                    (
                        organizacao_id,
                        nome,
                        email,
                        hash_da_senha(senha),
                        papel,
                        contatos["telegram_chat_id"],
                        contatos["whatsapp_telefone"],
                        operador_id,
                    ),
                ).fetchone()["id"]
                acao = "usuario_criar"
            conn.execute("DELETE FROM erp.usuario_fazendas WHERE usuario_id = %s", (usuario_id,))
            for f in sorted(fazendas):
                conn.execute(
                    "INSERT INTO erp.usuario_fazendas (usuario_id, fazenda_id) VALUES (%s, %s)",
                    (usuario_id, f),
                )
            _sincronizar_gerente(conn, usuario_id)
            depois = conn.execute("SELECT * FROM erp.usuarios WHERE id = %s", (usuario_id,)).fetchone()
            registrar_auditoria(
                conn,
                None,
                "painel",
                acao,
                _visivel(antes),
                {**_visivel(depois), "fazendas": sorted(fazendas)},
                f"por {por['nome']} (usuário {por['id']})" + (", senha trocada" if senha and antes else ""),
            )
    except psycopg.errors.UniqueViolation:
        return Resultado(False, {"email": "Já existe um usuário com este e-mail."})
    return Resultado(True, {}, usuario_id)


def alternar_usuario(usuario_id: int, ativo: bool, organizacao_id: int, por: dict[str, Any]) -> str | None:
    """Desativa ou reativa. Devolve o motivo da recusa, ou `None`. Desativar encerra as sessões."""
    if usuario_id == por["id"] and not ativo:
        return "Você não pode desativar a si mesmo."
    with conectar() as conn:
        antes = conn.execute(
            "SELECT * FROM erp.usuarios WHERE id = %s AND organizacao_id = %s FOR UPDATE",
            (usuario_id, organizacao_id),
        ).fetchone()
        if not antes:
            return "Usuário não encontrado."
        if antes["ativo"] == ativo:
            return None
        conn.execute("UPDATE erp.usuarios SET ativo = %s WHERE id = %s", (ativo, usuario_id))
        if not ativo:
            conn.execute("DELETE FROM erp.sessoes WHERE usuario_id = %s", (usuario_id,))
        _sincronizar_gerente(conn, usuario_id)
        registrar_auditoria(
            conn,
            None,
            "painel",
            "usuario_reativar" if ativo else "usuario_desativar",
            _visivel(antes),
            {**_visivel(antes), "ativo": ativo},
            f"por {por['nome']} (usuário {por['id']})",
        )
    return None


def trocar_senha(usuario_id: int, atual: str, nova: str) -> str | None:
    """Troca a própria senha. Devolve o erro, ou `None`. Encerra as outras sessões do usuário."""
    if (msg := erro_de_senha(nova)) is not None:
        return msg
    with conectar() as conn:
        linha = conn.execute("SELECT senha_hash FROM erp.usuarios WHERE id = %s", (usuario_id,)).fetchone()
        if not linha or not senha_confere(atual, linha["senha_hash"]):
            return "A senha atual não confere."
        conn.execute(
            "UPDATE erp.usuarios SET senha_hash = %s WHERE id = %s", (hash_da_senha(nova), usuario_id)
        )
        conn.execute("DELETE FROM erp.sessoes WHERE usuario_id = %s", (usuario_id,))
        registrar_auditoria(conn, None, "painel", "usuario_trocar_senha", None, None, f"usuário {usuario_id}")
    return None


def criar_primeiro_gerente(nome: str, email: str, senha: str, organizacao: str | None = None) -> int:
    """Primeiro acesso (CLI e variáveis do `.env`): cria o gerente com todas as fazendas da organização.
    Sem organização ainda, cria a "Organização Exemplo" e a "Fazenda Exemplo"."""
    with conectar() as conn:
        org = conn.execute(
            "SELECT id FROM erp.organizacoes WHERE (%s::text IS NULL OR nome = %s) ORDER BY id LIMIT 1",
            (organizacao, organizacao),
        ).fetchone()
        if org is None:
            org = conn.execute(
                "INSERT INTO erp.organizacoes (nome) VALUES (%s) RETURNING id",
                (organizacao or "Organização Exemplo",),
            ).fetchone()
        if (
            conn.execute("SELECT 1 FROM erp.fazendas WHERE organizacao_id = %s", (org["id"],)).fetchone()
            is None
        ):
            conn.execute(
                "INSERT INTO erp.fazendas (organizacao_id, nome) VALUES (%s, 'Fazenda Exemplo')", (org["id"],)
            )
        fazendas = [
            f["id"]
            for f in conn.execute(
                "SELECT id FROM erp.fazendas WHERE organizacao_id = %s AND NOT demo ORDER BY id", (org["id"],)
            ).fetchall()
        ] or [
            f["id"]
            for f in conn.execute(
                "SELECT id FROM erp.fazendas WHERE organizacao_id = %s ORDER BY id", (org["id"],)
            ).fetchall()
        ]
    r = salvar_usuario(
        {"nome": nome, "email": email, "papel": "gerente", "senha": senha, "fazendas": fazendas},
        org["id"],
        {"id": 0, "nome": "linha de comando"},
    )
    if not r.ok:
        raise ValueError("; ".join(r.erros.values()))
    return r.id


def algum_usuario() -> bool:
    try:
        with conectar() as conn:
            return conn.execute("SELECT 1 FROM erp.usuarios LIMIT 1").fetchone() is not None
    except psycopg.errors.UndefinedTable:
        return True  # sem a tabela não há o que criar aqui


# --- ligação com o FastAPI ---------------------------------------------------------------------
def autenticacao_ligada() -> bool:
    """Ligada sempre, menos com `PAINEL_AUTENTICACAO=desligada` (só os testes e a demonstração local usam)."""
    return os.environ.get("PAINEL_AUTENTICACAO", "ligada").strip().lower() != "desligada"


def exigir(acao: str):
    """Dependência de rota: 401 sem login, 403 se o papel do usuário não tem a ação (tabela `PERMISSOES`)."""
    from fastapi import HTTPException, Request

    def dependencia(request: Request) -> None:
        usuario = getattr(request.state, "usuario", None)
        if usuario is None:
            if autenticacao_ligada():
                raise HTTPException(401, "Entre para continuar.")
            return
        if not pode(usuario["papel"], acao):
            raise HTTPException(403, "O seu perfil não tem permissão para esta ação.")

    return dependencia
