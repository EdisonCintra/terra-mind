"""F3: login por sessão, perfis (gerente e operador), fazendas por usuário, usuários e AGENTS_TOKEN."""

import psycopg
import pytest
from fastapi.testclient import TestClient

from painel import auth
from painel import main as painel_main
from tests.conftest import DATABASE_URL

SENHA = "Senha-Forte-1"
ORIGEM = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def autenticacao_ligada(monkeypatch):
    monkeypatch.setenv("PAINEL_AUTENTICACAO", "ligada")
    auth.zerar_limites()
    yield
    auth.zerar_limites()


def sql(consulta, params=None):
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        cur = conn.execute(consulta, params or None)
        return cur.fetchall() if cur.description else None


@pytest.fixture
def mundo():
    """Duas fazendas na organização do seed, uma em outra organização, e usuários de cada papel."""
    org = sql("SELECT id FROM erp.organizacoes ORDER BY id LIMIT 1")[0][0]
    sql("INSERT INTO erp.fazendas (organizacao_id, nome) VALUES (%s, 'Fazenda Segunda')", (org,))
    fazendas = [
        r[0]
        for r in sql("SELECT id FROM erp.fazendas WHERE organizacao_id = %s AND NOT demo ORDER BY id", (org,))
    ]
    outra_org = sql("INSERT INTO erp.organizacoes (nome) VALUES ('Outra') RETURNING id")[0][0]
    outra_faz = sql(
        "INSERT INTO erp.fazendas (organizacao_id, nome) VALUES (%s, 'Fazenda Alheia') RETURNING id",
        (outra_org,),
    )[0][0]

    def criar(nome, email, papel, fazs, organizacao=org):
        r = auth.salvar_usuario(
            {"nome": nome, "email": email, "papel": papel, "senha": SENHA, "fazendas": fazs},
            organizacao,
            {"id": 0, "nome": "teste"},
        )
        assert r.ok, r.erros
        return r.id

    return {
        "org": org,
        "faz": fazendas,
        "outra_faz": outra_faz,
        "gerente": criar("Gerente", "gerente@exemplo.com", "gerente", fazendas),
        "operador": criar("Operador", "operador@exemplo.com", "operador", fazendas[:1]),
        "alheio": criar("Alheio", "alheio@outra.com", "gerente", [outra_faz], outra_org),
        "criar": criar,
    }


def incidente(talhao_id=1):
    """Incidente aguardando aprovação, com um plano sem resposta."""
    inc = sql(
        "INSERT INTO agente.incidentes (talhao_id, gatilho, previsao_snapshot, status)"
        " VALUES (%s, '[]', '[]', 'aguardando_aprovacao') RETURNING id",
        (talhao_id,),
    )[0][0]
    plano = sql(
        "INSERT INTO agente.planos (incidente_id, opcoes_json) VALUES (%s, %s::jsonb) RETURNING id",
        (inc, '{"mensagem": "plano"}'),
    )[0][0]
    return inc, plano


def talhao_alheio(mundo):
    return sql(
        "INSERT INTO erp.talhoes"
        " (fazenda_id, nome, cultura, data_plantio, estagio_fenologico, lat, lon, area_ha)"
        " VALUES (%s, 'Alheio', 'soja', '2026-10-01', 'V6', -17, -49, 10) RETURNING id",
        (mundo["outra_faz"],),
    )[0][0]


def entrar(email, senha=SENHA, **kw):
    cliente = TestClient(painel_main.app, follow_redirects=False)
    r = cliente.post("/login", data={"email": email, "senha": senha, **kw}, headers=ORIGEM)
    return cliente, r


def como(email):
    cliente, r = entrar(email)
    assert r.status_code == 303, r.text
    return cliente


# --- senha ------------------------------------------------------------------------------------
def test_hash_nao_guarda_a_senha_e_confere():
    h = auth.hash_da_senha(SENHA)
    assert SENHA not in h and h.startswith("scrypt$")
    assert auth.senha_confere(SENHA, h)
    assert not auth.senha_confere("outra-senha", h)
    assert not auth.senha_confere(SENHA, "lixo")
    assert auth.hash_da_senha(SENHA) != h  # sal diferente a cada vez


@pytest.mark.parametrize("senha", ["curta1", "12345678", " espaco-nas-pontas ", "x" * 200])
def test_senha_fraca_recusada(senha):
    assert auth.erro_de_senha(senha)


# --- porteiro -----------------------------------------------------------------------------------
def test_sem_login_pagina_vai_para_o_login_com_retorno(mundo):
    r = TestClient(painel_main.app, follow_redirects=False).get("/mapa")
    assert r.status_code == 303
    assert r.headers["location"] == "/login?proximo=%2Fmapa"


def test_sem_login_htmx_e_api_recebem_401(mundo):
    c = TestClient(painel_main.app, follow_redirects=False)
    assert c.get("/parcial/os").status_code == 401
    assert c.get("/api/risco").status_code == 401
    r = c.get("/visao-geral", headers={"HX-Request": "true"})
    assert r.status_code == 401 and r.headers["HX-Redirect"] == "/login"


def test_health_login_e_estaticos_ficam_abertos(mundo):
    c = TestClient(painel_main.app, follow_redirects=False)
    assert c.get("/health").status_code == 200
    assert c.get("/login").status_code == 200
    assert c.get("/static/painel.css").status_code == 200


def test_login_certo_abre_sessao_com_cookie_httponly(mundo):
    cliente, r = entrar("gerente@exemplo.com")
    assert r.status_code == 303 and r.headers["location"] == "/"
    cookie = r.headers["set-cookie"].lower()
    assert "tm_sessao=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert cliente.get("/visao-geral").status_code == 200
    # o banco guarda só o hash do token
    token = cliente.cookies["tm_sessao"]
    assert sql("SELECT count(*) FROM erp.sessoes WHERE token_hash = %s", (token,))[0][0] == 0
    assert sql("SELECT count(*) FROM erp.sessoes")[0][0] == 1


def test_login_volta_para_a_pagina_pedida_mas_so_dentro_do_painel(mundo):
    _, r = entrar("gerente@exemplo.com", proximo="/mapa")
    assert r.headers["location"] == "/mapa"
    for ruim in ("https://evil.example", "//evil.example", "/\\evil"):
        _, r = entrar("gerente@exemplo.com", proximo=ruim)
        assert r.headers["location"] == "/"


def test_login_errado_nao_diz_se_o_email_existe(mundo):
    _, r1 = entrar("gerente@exemplo.com", "senha-errada")
    _, r2 = entrar("ninguem@exemplo.com", "qualquer-coisa")
    assert r1.status_code == r2.status_code == 401
    assert "incorretos" in r1.text and "incorretos" in r2.text
    assert "tm_sessao" not in r1.headers.get("set-cookie", "")


def test_usuario_desativado_nao_entra(mundo):
    sql("UPDATE erp.usuarios SET ativo = false WHERE id = %s", (mundo["operador"],))
    _, r = entrar("operador@exemplo.com")
    assert r.status_code == 401


def test_bloqueia_depois_de_cinco_erros_e_o_acerto_nao_vale_no_bloqueio(mundo):
    for _ in range(auth.MAX_POR_EMAIL):
        assert entrar("gerente@exemplo.com", "errada")[1].status_code == 401
    assert entrar("gerente@exemplo.com")[1].status_code == 429


def test_sessao_expirada_ou_de_usuario_desativado_nao_vale(mundo):
    cliente = como("gerente@exemplo.com")
    sql("UPDATE erp.sessoes SET expira_em = now() - interval '1 minute'")
    assert cliente.get("/mapa").status_code == 303
    cliente = como("gerente@exemplo.com")
    sql("UPDATE erp.usuarios SET ativo = false WHERE id = %s", (mundo["gerente"],))
    assert cliente.get("/mapa").status_code == 303


def test_logout_encerra_a_sessao_no_servidor(mundo):
    cliente = como("gerente@exemplo.com")
    token = cliente.cookies["tm_sessao"]
    assert cliente.post("/logout", headers=ORIGEM).status_code == 303
    antigo = TestClient(painel_main.app, follow_redirects=False)
    antigo.cookies.set("tm_sessao", token)
    assert antigo.get("/mapa").status_code == 303
    assert sql("SELECT count(*) FROM erp.sessoes")[0][0] == 0


# --- CSRF -------------------------------------------------------------------------------------
def test_post_sem_origem_ou_de_outro_site_e_recusado(mundo):
    cliente = como("gerente@exemplo.com")
    assert cliente.post("/fazenda/selecionar", data={"fazenda_id": mundo["faz"][0]}).status_code == 403
    r = cliente.post(
        "/fazenda/selecionar",
        data={"fazenda_id": mundo["faz"][0]},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    r = cliente.post(
        "/fazenda/selecionar",
        data={"fazenda_id": mundo["faz"][0]},
        headers={"Referer": "http://testserver/mapa"},
    )
    assert r.status_code == 303


# --- perfis ------------------------------------------------------------------------------------
def test_operador_nao_ve_cadastros_nem_usuarios_nem_aprova(mundo):
    op = como("operador@exemplo.com")
    assert op.get("/cadastros/talhoes").status_code == 403
    assert op.get("/cadastros").status_code == 403
    assert op.post("/cadastros/talhoes", data={"nome": "x"}, headers=ORIGEM).status_code == 403
    assert op.get("/usuarios").status_code == 403
    assert op.post("/usuarios", data={"nome": "x"}, headers=ORIGEM).status_code == 403
    assert (
        op.post("/aprovacao/1/1/SIM", headers=ORIGEM).status_code == 403
    )  # "pronto quando": 403 direto na rota
    menu = op.get("/visao-geral").text
    assert 'href="/cadastros"' not in menu and 'href="/usuarios"' not in menu
    assert 'href="/cadastros/os"' in menu


def test_operador_ve_e_cria_e_edita_ordens(mundo):
    op = como("operador@exemplo.com")
    for rota in (
        "/visao-geral",
        "/mapa",
        "/agenda",
        "/fazenda",
        "/historico",
        "/relatorios",
        "/cadastros/os",
    ):
        assert op.get(rota).status_code == 200, rota
    # só a aba de OS aparece para ele
    html = op.get("/cadastros/os").text
    assert 'href="/cadastros/talhoes"' not in html


def test_gerente_acessa_cadastros_e_usuarios(mundo):
    g = como("gerente@exemplo.com")
    assert g.get("/cadastros/talhoes").status_code == 200
    assert g.get("/usuarios").status_code == 200
    assert 'href="/usuarios"' in g.get("/visao-geral").text


def test_operador_nao_ve_botoes_de_aprovacao(mundo):
    inc, plano = incidente()
    html_op = como("operador@exemplo.com").get("/parcial/pendentes").text
    html_g = como("gerente@exemplo.com").get("/parcial/pendentes").text
    assert "Só o gerente responde" in html_op and "/aprovacao/" not in html_op
    assert f"/aprovacao/{inc}/{plano}/SIM" in html_g


# --- aprovação: nome de quem aprovou e AGENTS_TOKEN ----------------------------------------------
class _Resposta:
    status_code = 200
    is_success = True


class _Http:
    chamadas: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, headers=None, **kw):
        _Http.chamadas.append((url, json, headers))
        return _Resposta()


def test_aprovacao_manda_o_nome_do_usuario_e_o_segredo(mundo, monkeypatch):
    incidente()
    _Http.chamadas = []
    monkeypatch.setattr(painel_main, "cliente_http", lambda *a, **k: _Http())
    monkeypatch.setenv("AGENTS_TOKEN", "segredo-123")
    r = como("gerente@exemplo.com").post("/aprovacao/1/7/SIM", headers=ORIGEM)
    assert r.status_code == 200
    url, corpo, cab = _Http.chamadas[0]
    assert corpo["respondido_por"] == "Gerente" and cab == {"X-Agents-Token": "segredo-123"}


def test_aprovacao_de_incidente_de_outra_fazenda_da_404(mundo, monkeypatch):
    talhao = talhao_alheio(mundo)
    incidente(talhao)
    _Http.chamadas = []
    monkeypatch.setattr(painel_main, "cliente_http", lambda *a, **k: _Http())
    r = como("gerente@exemplo.com").post("/aprovacao/1/7/SIM", headers=ORIGEM)
    assert r.status_code == 404 and not _Http.chamadas


def test_agents_exige_o_segredo_so_na_resposta(monkeypatch):
    from agents import api
    from agents.api import Servicos, criar_api

    class Deps:
        class settings:  # noqa: N801
            agents_token = "segredo-123"

    class Orq:
        deps = Deps()

        async def responder(self, r):
            return "ok"

        async def aguardar(self):
            pass

    async def fabrica():
        return Servicos(orquestrador=Orq())

    with TestClient(criar_api(fabrica)) as c:
        corpo = {"plano_id": 1, "resposta": "SIM"}
        assert c.post("/incidentes/1/resposta", json=corpo).status_code == 401
        assert (
            c.post("/incidentes/1/resposta", json=corpo, headers={"X-Agents-Token": "errado"}).status_code
            == 401
        )
        assert (
            c.post(
                "/incidentes/1/resposta", json=corpo, headers={"X-Agents-Token": "segredo-123"}
            ).status_code
            == 200
        )
        assert c.get("/health").status_code == 200
    assert api.conferir_token  # a dependência existe e é a única na rota de resposta


def test_agents_sem_segredo_configurado_segue_aberto():
    from agents.api import Servicos, criar_api

    class Deps:
        class settings:  # noqa: N801
            agents_token = None

    class Orq:
        deps = Deps()

        async def responder(self, r):
            return "ok"

        async def aguardar(self):
            pass

    async def fabrica():
        return Servicos(orquestrador=Orq())

    with TestClient(criar_api(fabrica)) as c:
        assert c.post("/incidentes/1/resposta", json={"plano_id": 1, "resposta": "SIM"}).status_code == 200


# --- fazendas do usuário ---------------------------------------------------------------------------
def test_usuario_so_ve_as_proprias_fazendas_no_seletor_e_sem_todas(mundo):
    html = como("operador@exemplo.com").get("/visao-geral").text
    assert f'value="{mundo["faz"][0]}"' in html
    assert f'value="{mundo["faz"][1]}"' not in html and "Todas as fazendas" not in html
    assert "Fazenda Alheia" not in html


def test_cookie_de_fazenda_alheia_cai_na_primeira_permitida(mundo):
    op = como("operador@exemplo.com")
    op.cookies.set("fazenda_id", str(mundo["faz"][1]))
    assert f'<option value="{mundo["faz"][0]}" selected>' in op.get("/visao-geral").text
    op.cookies.set("fazenda_id", str(mundo["outra_faz"]))
    assert "Alheia" not in op.get("/visao-geral").text


def test_registro_de_outra_organizacao_da_404(mundo):
    talhao = talhao_alheio(mundo)
    g = como("gerente@exemplo.com")
    assert g.get(f"/api/previsao/{talhao}").status_code == 404
    assert g.get(f"/parcial/mapa/talhao/{talhao}").status_code == 404
    assert g.get(f"/parcial/clima?talhao_id={talhao}").status_code == 404
    seu = sql("SELECT id FROM erp.talhoes WHERE fazenda_id = %s LIMIT 1", (mundo["faz"][0],))[0][0]
    assert g.get(f"/parcial/mapa/talhao/{seu}").status_code == 200


def test_os_e_relatorio_de_outra_fazenda_da_404(mundo):
    so_da_segunda = mundo["criar"]("Só da segunda", "segunda@exemplo.com", "gerente", [mundo["faz"][1]])
    assert so_da_segunda
    assert (
        como("segunda@exemplo.com").get("/os/1/concluir").status_code == 404
    )  # a OS 1 é da primeira fazenda
    assert como("gerente@exemplo.com").get("/os/1/concluir").status_code == 200
    assert como("gerente@exemplo.com").get("/relatorios/99999/pdf").status_code == 404


# --- usuários (cadastro) ----------------------------------------------------------------------------
def novo_usuario(cliente, **extra):
    dados = {
        "nome": "Nova Pessoa",
        "email": "nova@exemplo.com",
        "papel": "operador",
        "senha": SENHA,
        "fazendas": [],
    }
    dados.update(extra)
    return cliente.post("/usuarios", data=dados, headers=ORIGEM)


def test_gerente_cria_usuario_que_entra_e_so_ve_as_fazendas_marcadas(mundo):
    g = como("gerente@exemplo.com")
    r = novo_usuario(g, fazendas=[str(mundo["faz"][0])])
    assert r.status_code == 303 and r.headers["location"] == "/usuarios?ok=criado"
    nova = como("nova@exemplo.com")
    assert nova.get("/visao-geral").status_code == 200
    hash_guardado = sql("SELECT senha_hash FROM erp.usuarios WHERE email = 'nova@exemplo.com'")[0][0]
    assert SENHA not in hash_guardado


def test_usuario_novo_exige_senha_boa_email_valido_e_fazenda(mundo):
    g = como("gerente@exemplo.com")
    r = novo_usuario(g, senha="123", email="sem-arroba", fazendas=[])
    assert r.status_code == 422
    for texto in ("pelo menos 8", "e-mail válido", "pelo menos uma fazenda"):
        assert texto in r.text


def test_email_repetido_recusado(mundo):
    g = como("gerente@exemplo.com")
    r = novo_usuario(g, email="OPERADOR@exemplo.com", fazendas=[str(mundo["faz"][0])])
    assert r.status_code == 422 and "Já existe" in r.text


def test_gerente_nao_liga_usuario_a_fazenda_de_outra_organizacao(mundo):
    g = como("gerente@exemplo.com")
    r = novo_usuario(g, fazendas=[str(mundo["outra_faz"])])
    assert r.status_code == 422 and "outra organização" in r.text
    assert not sql("SELECT 1 FROM erp.usuarios WHERE email = 'nova@exemplo.com'")


def test_gerente_nao_enxerga_nem_edita_usuario_de_outra_organizacao(mundo):
    g = como("gerente@exemplo.com")
    assert "alheio@outra.com" not in g.get("/usuarios").text
    assert g.get(f"/usuarios?editar={mundo['alheio']}").status_code == 404
    r = g.post(f"/usuarios/{mundo['alheio']}/desativar", headers=ORIGEM)
    assert r.status_code == 422 and "não encontrado" in r.text
    assert sql("SELECT ativo FROM erp.usuarios WHERE id = %s", (mundo["alheio"],))[0][0] is True


def test_nao_desativa_a_si_mesmo_nem_muda_o_proprio_papel(mundo):
    g = como("gerente@exemplo.com")
    r = g.post(f"/usuarios/{mundo['gerente']}/desativar", headers=ORIGEM)
    assert r.status_code == 422 and "a si mesmo" in r.text
    r = g.post(
        f"/usuarios/{mundo['gerente']}",
        data={
            "nome": "Gerente",
            "email": "gerente@exemplo.com",
            "papel": "operador",
            "fazendas": [str(f) for f in mundo["faz"]],
        },
        headers=ORIGEM,
    )
    assert r.status_code == 422 and "próprio papel" in r.text


def test_desativar_encerra_as_sessoes_na_hora(mundo):
    g = como("gerente@exemplo.com")
    op = como("operador@exemplo.com")
    assert op.get("/mapa").status_code == 200
    assert g.post(f"/usuarios/{mundo['operador']}/desativar", headers=ORIGEM).status_code == 303
    assert op.get("/mapa").status_code == 303
    assert g.post(f"/usuarios/{mundo['operador']}/reativar", headers=ORIGEM).status_code == 303
    como("operador@exemplo.com")


def test_editar_sem_senha_mantem_a_senha_e_com_senha_troca(mundo):
    g = como("gerente@exemplo.com")
    base = {
        "nome": "Operador Novo",
        "email": "operador@exemplo.com",
        "papel": "operador",
        "fazendas": [str(mundo["faz"][0])],
    }
    assert (
        g.post(f"/usuarios/{mundo['operador']}", data={**base, "senha": ""}, headers=ORIGEM).status_code
        == 303
    )
    como("operador@exemplo.com")
    assert (
        g.post(
            f"/usuarios/{mundo['operador']}", data={**base, "senha": "Outra-Senha-9"}, headers=ORIGEM
        ).status_code
        == 303
    )
    assert entrar("operador@exemplo.com")[1].status_code == 401
    assert entrar("operador@exemplo.com", "Outra-Senha-9")[1].status_code == 303


def test_usuario_grava_auditoria_sem_hash(mundo):
    g = como("gerente@exemplo.com")
    novo_usuario(g, fazendas=[str(mundo["faz"][0])])
    linhas = sql(
        "SELECT acao, depois::text, justificativa FROM agente.auditoria"
        " WHERE acao = 'usuario_criar' ORDER BY id DESC"
    )
    assert linhas and "scrypt" not in linhas[0][1] and "senha_hash" not in linhas[0][1]
    assert "Gerente" in linhas[0][2]


# --- contato dos canais ------------------------------------------------------------------------------
def test_gerente_com_contato_vira_destino_do_canal_e_desativar_tira(mundo):
    from channels import destinos

    g = como("gerente@exemplo.com")
    r = g.post(
        f"/usuarios/{mundo['gerente']}",
        data={
            "nome": "Gerente",
            "email": "gerente@exemplo.com",
            "papel": "gerente",
            "telegram_chat_id": "777000",
            "whatsapp_telefone": "5564999998888",
            "fazendas": [str(mundo["faz"][0])],
        },
        headers=ORIGEM,
    )
    assert r.status_code == 303
    assert 777000 in destinos.chats_dos_gerentes(mundo["faz"][0])
    assert 5564999998888 in destinos.chats_dos_gerentes(mundo["faz"][0], destinos.CONTATO_WHATSAPP)
    assert 777000 not in destinos.chats_dos_gerentes(mundo["faz"][1])  # fazenda desmarcada
    # operador com contato não vira quem aprova
    mundo["criar"]("Op", "op2@exemplo.com", "operador", mundo["faz"][:1])
    sql("UPDATE erp.usuarios SET telegram_chat_id = 555 WHERE email = 'op2@exemplo.com'")
    assert 555 not in destinos.todos_os_chats_de_gerentes()


# --- conta -----------------------------------------------------------------------------------------------
def test_trocar_a_propria_senha_encerra_as_outras_sessoes(mundo):
    a, b = como("operador@exemplo.com"), como("operador@exemplo.com")
    r = a.post(
        "/conta/senha",
        data={"atual": SENHA, "nova": "Nova-Senha-77", "confirmacao": "Nova-Senha-77"},
        headers=ORIGEM,
    )
    assert r.status_code == 303
    assert b.get("/mapa").status_code == 303  # a outra sessão caiu
    assert a.cookies.get("tm_sessao") and a.get("/mapa").status_code == 200  # esta ganhou uma nova
    assert entrar("operador@exemplo.com", "Nova-Senha-77")[1].status_code == 303


def test_trocar_senha_exige_a_atual_e_a_confirmacao(mundo):
    c = como("operador@exemplo.com")
    r = c.post(
        "/conta/senha",
        data={"atual": "errada", "nova": "Nova-Senha-77", "confirmacao": "Nova-Senha-77"},
        headers=ORIGEM,
    )
    assert r.status_code == 422 and "não confere" in r.text
    r = c.post(
        "/conta/senha",
        data={"atual": SENHA, "nova": "Nova-Senha-77", "confirmacao": "diferente"},
        headers=ORIGEM,
    )
    assert r.status_code == 422 and "confirmação" in r.text


# --- primeiro acesso -------------------------------------------------------------------------------------
def test_cria_o_primeiro_gerente_pelas_variaveis_so_quando_nao_ha_usuario(monkeypatch):
    from painel.criar_usuario import inicializar

    monkeypatch.setenv("PAINEL_ADMIN_EMAIL", "primeiro@exemplo.com")
    monkeypatch.setenv("PAINEL_ADMIN_SENHA", SENHA)
    inicializar()
    assert sql("SELECT papel FROM erp.usuarios WHERE email = 'primeiro@exemplo.com'") == [("gerente",)]
    inicializar()  # de novo: não duplica nem falha
    assert sql("SELECT count(*) FROM erp.usuarios")[0][0] == 1
    assert entrar("primeiro@exemplo.com")[1].status_code == 303
