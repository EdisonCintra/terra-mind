"""Canal WhatsApp (F4, contratos 6, 7 e 20) pela WhatsApp Cloud API da Meta.

Mesma interface do Telegram (`NotificationChannel`): os nós do `agents` não mudam. Três diferenças:

1. **A resposta chega por webhook HTTPS público**, não por polling. O `agents` expõe
   `GET/POST /whatsapp/webhook` (ver `agents/api.py`) e entrega ao canal; o `POST` só vale com a
   assinatura `X-Hub-Signature-256` da Meta (HMAC-SHA256 do corpo com o App Secret), senão 403.
2. **Fora da janela de 24 h** desde a última mensagem do contato, só sai mensagem de modelo (template)
   aprovada pela Meta. Dentro dela, texto livre e botões de resposta. O canal guarda a última mensagem
   recebida de cada telefone (`agente.whatsapp_janelas`) e escolhe a forma; se a Meta recusar a mensagem
   livre por janela fechada (erro 131047), cai para o modelo. Os modelos e como cadastrá-los estão em
   `docs/whatsapp.md`.
3. **Cada mensagem de modelo é cobrada** pela Meta (custo a levantar no BMC).

Destino (contrato 20): como no Telegram, o gerente da fazenda do incidente (`erp.gerentes.whatsapp_telefone`);
`WHATSAPP_GERENTE_TELEFONE` é só o gerente padrão. Só o telefone de um gerente da fazenda do incidente aprova.

Mensagem de texto de um gerente (ou de um número em `WHATSAPP_NUMEROS_BULA`) é uma pergunta à bula (G4).
"""

import asyncio
import hashlib
import hmac
import logging
import os
from collections import OrderedDict
from dataclasses import replace
from typing import Any

import httpx

from channels import destinos
from channels.base import (
    DECISAO_JA_REGISTRADA,
    ECO,
    ROTULOS,
    AoResponder,
    DecisaoJaRegistrada,
    NotificationChannel,
    ler_callback,
    montar_callback,
)
from channels.destinos import CONTATO_WHATSAPP
from erp_mcp.db import conectar

log = logging.getLogger(__name__)

URL_GRAPH = "https://graph.facebook.com"
VERSAO_PADRAO = "v22.0"  # confira a versão vigente da Graph API no painel da Meta (WHATSAPP_API_VERSION)

LIMITE_CORPO_BOTOES = 1024  # corpo de mensagem interativa
LIMITE_TEXTO = 4096  # mensagem de texto livre
LIMITE_PARAMETRO = 1000  # variável do modelo (a Meta limita o corpo todo a 1024)
JANELA_H = 24
MARGEM_JANELA_MIN = 5  # trata a janela como fechada um pouco antes, para a mensagem não chegar na borda
ERRO_FORA_DA_JANELA = 131047  # "Re-engagement message": mensagem livre fora das 24 h

AJUDA = (
    "Pergunte à bula: escreva sua dúvida citando o produto.\n"
    "Ex.: Posso aplicar Abacus HC com vento de 12 km/h?\n"
    "Toda resposta vem com a bula e a página. Se a bula não trouxer o assunto, eu aviso."
)
NAO_AUTORIZADO = "Este número ainda não está liberado para perguntas. Peça ao gerente da fazenda."
SEM_PERGUNTA = "As perguntas à bula ainda não estão ligadas neste canal."
SO_GERENTE_APROVA = "Só o gerente da fazenda deste incidente pode aprovar ou recusar o plano."


class WhatsAppErro(Exception):
    """A Cloud API recusou o envio. `codigo` é o código de erro da Meta (ex.: 131047)."""

    def __init__(self, mensagem: str, codigo: int | None = None):
        super().__init__(mensagem)
        self.codigo = codigo


def achatar(texto: str, limite: int = LIMITE_PARAMETRO) -> str:
    """Variável de modelo não aceita quebra de linha nem 4+ espaços seguidos: junta as linhas com " · "."""
    partes = [p.strip() for p in texto.replace("\t", " ").splitlines() if p.strip()]
    plano = " · ".join(partes)
    while "    " in plano:
        plano = plano.replace("    ", "   ")
    return plano if len(plano) <= limite else plano[: limite - 1] + "…"


def cortar(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def _gravar_recebida(telefone: int) -> None:
    with conectar() as conn:
        conn.execute(
            "INSERT INTO agente.whatsapp_janelas (telefone, ultima_mensagem_em) VALUES (%s, now())"
            " ON CONFLICT (telefone) DO UPDATE SET ultima_mensagem_em = now()",
            (telefone,),
        )


def _janela_aberta(telefone: int) -> bool:
    with conectar() as conn:
        linha = conn.execute(
            "SELECT ultima_mensagem_em > now() - make_interval(hours => %s, mins => -%s) AS aberta"
            " FROM agente.whatsapp_janelas WHERE telefone = %s",
            (JANELA_H, MARGEM_JANELA_MIN, telefone),
        ).fetchone()
    return bool(linha and linha["aberta"])


class WhatsAppChannel(NotificationChannel):
    campo_operador = CONTATO_WHATSAPP

    def __init__(
        self,
        ao_responder: AoResponder,
        token: str | None = None,
        phone_number_id: str | None = None,
        app_secret: str | None = None,
        verify_token: str | None = None,
        gerente_telefone: int | None = None,
        numeros_bula: str | None = None,
        template_aprovacao: str | None = None,
        template_aviso: str | None = None,
        idioma: str | None = None,
        versao: str | None = None,
        cliente: httpx.AsyncClient | None = None,
    ):
        env = os.environ.get
        self.ao_responder = ao_responder
        self.token = token or env("WHATSAPP_TOKEN", "")
        self.phone_number_id = phone_number_id or env("WHATSAPP_PHONE_NUMBER_ID", "")
        if not self.token or not self.phone_number_id:
            raise RuntimeError(
                "CHANNEL=whatsapp exige WHATSAPP_TOKEN e WHATSAPP_PHONE_NUMBER_ID preenchidos; "
                "sem eles use CHANNEL=painel ou CHANNEL=telegram"
            )
        # Sem App Secret o webhook não aceita nada (fail closed): ninguém aprova plano sem assinatura.
        self.app_secret = app_secret if app_secret is not None else env("WHATSAPP_APP_SECRET", "")
        self.verify_token = verify_token if verify_token is not None else env("WHATSAPP_VERIFY_TOKEN", "")
        padrao = gerente_telefone if gerente_telefone is not None else env("WHATSAPP_GERENTE_TELEFONE", "")
        self.gerente_padrao = int(padrao) if str(padrao).strip() else None
        extras = numeros_bula if numeros_bula is not None else env("WHATSAPP_NUMEROS_BULA", "")
        self.numeros_pergunta = {
            *([self.gerente_padrao] if self.gerente_padrao is not None else []),
            *(int(n) for n in str(extras).replace(" ", "").split(",") if n),
        }
        self.template_aprovacao = template_aprovacao or env(
            "WHATSAPP_TEMPLATE_APROVACAO", "terramind_aprovacao"
        )
        self.template_aviso = template_aviso or env("WHATSAPP_TEMPLATE_AVISO", "terramind_aviso")
        self.idioma = idioma or env("WHATSAPP_TEMPLATE_IDIOMA", "pt_BR")
        versao = versao or env("WHATSAPP_API_VERSION", VERSAO_PADRAO)
        self.url = f"{env('WHATSAPP_API_URL', URL_GRAPH)}/{versao}/{self.phone_number_id}/messages"
        self.http = cliente or httpx.AsyncClient(timeout=20)
        self._vistas: OrderedDict[str, None] = OrderedDict()  # a Meta reenvia o webhook se demorar
        self._tarefas: set[asyncio.Task] = set()

    # --- envio ------------------------------------------------------------------------------
    async def _post(self, corpo: dict[str, Any]) -> None:
        resposta = await self.http.post(
            self.url,
            json={"messaging_product": "whatsapp", **corpo},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        if resposta.is_success:
            return
        try:
            erro = resposta.json().get("error", {})
        except ValueError:
            erro = {}
        raise WhatsAppErro(
            f"WhatsApp recusou o envio (HTTP {resposta.status_code}): "
            f"{erro.get('message', resposta.text[:200])}",
            erro.get("code"),
        )

    async def _texto(self, telefone: int, texto: str) -> None:
        await self._post(
            {
                "to": str(telefone),
                "type": "text",
                "text": {"body": cortar(texto, LIMITE_TEXTO), "preview_url": False},
            }
        )

    async def _botoes(self, telefone: int, incidente_id: int, plano_id: int, texto: str) -> None:
        """Mensagem com os 3 botões (só dentro da janela de 24 h). Corpo longo vai antes, como texto."""
        corpo = texto
        if len(texto) > LIMITE_CORPO_BOTOES:
            await self._texto(telefone, texto)
            corpo = f"Plano {plano_id} (incidente {incidente_id}): escolha uma opção."
        await self._post(
            {
                "to": str(telefone),
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": corpo},
                    "action": {
                        "buttons": [
                            {
                                "type": "reply",
                                "reply": {"id": montar_callback(incidente_id, plano_id, r), "title": rotulo},
                            }
                            for r, rotulo in ROTULOS.items()
                        ]
                    },
                },
            }
        )

    async def _modelo(self, telefone: int, nome: str, texto: str, botoes: list[str] | None = None) -> None:
        """Modelo aprovado: `texto` é a variável {{1}}; `botoes`, os payloads das respostas rápidas."""
        componentes: list[dict[str, Any]] = [
            {"type": "body", "parameters": [{"type": "text", "text": achatar(texto)}]}
        ]
        for indice, payload in enumerate(botoes or []):
            componentes.append(
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": str(indice),
                    "parameters": [{"type": "payload", "payload": payload}],
                }
            )
        await self._post(
            {
                "to": str(telefone),
                "type": "template",
                "template": {"name": nome, "language": {"code": self.idioma}, "components": componentes},
            }
        )

    async def _livre_ou_modelo(self, telefone: int, livre, modelo) -> None:
        """Dentro da janela, a mensagem livre; fora dela (ou se a Meta recusar por janela), o modelo."""
        if await asyncio.to_thread(_janela_aberta, telefone):
            try:
                await livre()
                return
            except WhatsAppErro as e:
                if e.codigo != ERRO_FORA_DA_JANELA:
                    raise
                log.info("janela fechada para %s; enviando o modelo", telefone)
        await modelo()

    async def _gerentes(self, incidente_id: int | None) -> list[int]:
        return await asyncio.to_thread(
            destinos.destinos_do_incidente, incidente_id, self.gerente_padrao, CONTATO_WHATSAPP
        )

    @staticmethod
    async def _para_todos(telefones: list[int], enviar) -> None:
        """Manda a cada telefone; uma falha não impede os outros. Só levanta se NENHUM recebeu."""
        resultados = await asyncio.gather(*(enviar(t) for t in telefones), return_exceptions=True)
        falhas = [r for r in resultados if isinstance(r, BaseException)]
        for falha in falhas:
            log.error("falha ao enviar pelo WhatsApp", exc_info=falha)
        if falhas and len(falhas) == len(resultados):
            raise falhas[0]

    async def enviar_alerta(self, texto: str, incidente_id: int | None = None) -> None:
        if incidente_id is None:
            incidente_id = destinos.incidente_do_texto(texto)

        async def enviar(telefone: int) -> None:
            await self._livre_ou_modelo(
                telefone,
                lambda: self._texto(telefone, texto),
                lambda: self._modelo(telefone, self.template_aviso, texto),
            )

        await self._para_todos(await self._gerentes(incidente_id), enviar)

    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None:
        payloads = [montar_callback(incidente_id, plano_id, r) for r in ROTULOS]

        async def enviar(telefone: int) -> None:
            await self._livre_ou_modelo(
                telefone,
                lambda: self._botoes(telefone, incidente_id, plano_id, texto),
                lambda: self._modelo(telefone, self.template_aprovacao, texto, payloads),
            )

        await self._para_todos(await self._gerentes(incidente_id), enviar)

    async def notificar_operador(self, chat_id: int, texto: str) -> None:
        """`chat_id` é o telefone do operador (`erp.operadores.whatsapp_telefone`)."""
        await self._livre_ou_modelo(
            chat_id,
            lambda: self._texto(chat_id, texto),
            lambda: self._modelo(chat_id, self.template_aviso, texto),
        )

    async def _responder(self, telefone: int, texto: str) -> None:
        """Resposta a uma mensagem que o contato acabou de mandar (janela aberta): nunca derruba o webhook."""
        try:
            await self._texto(telefone, texto)
        except Exception:  # noqa: BLE001
            log.exception("falha ao responder no WhatsApp para %s", telefone)

    # --- webhook ----------------------------------------------------------------------------
    def verificar(self, modo: str, token: str, desafio: str) -> str | None:
        """`GET` de verificação da Meta: devolve o `hub.challenge` se o token confere, senão `None` (403)."""
        if modo == "subscribe" and self.verify_token and hmac.compare_digest(token, self.verify_token):
            return desafio
        return None

    def assinatura_valida(self, corpo: bytes, cabecalho: str | None) -> bool:
        """Confere `X-Hub-Signature-256` (HMAC-SHA256 do corpo com o App Secret); sem segredo, nada vale."""
        if not self.app_secret or not cabecalho or not cabecalho.startswith("sha256="):
            return False
        esperado = hmac.new(self.app_secret.encode(), corpo, hashlib.sha256).hexdigest()
        return hmac.compare_digest(esperado, cabecalho.removeprefix("sha256="))

    def agendar(self, payload: dict[str, Any]) -> None:
        """Processa em segundo plano: o webhook responde 200 logo, ou a Meta reenvia a mensagem."""
        tarefa = asyncio.get_running_loop().create_task(self._processar_seguro(payload))
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)

    async def aguardar(self) -> None:
        """Espera o que está em segundo plano (desligamento e testes)."""
        while self._tarefas:
            await asyncio.gather(*list(self._tarefas), return_exceptions=True)

    async def _processar_seguro(self, payload: dict[str, Any]) -> None:
        try:
            await self.processar(payload)
        except Exception:  # noqa: BLE001
            log.exception("falha ao processar o webhook do WhatsApp")

    async def processar(self, payload: dict[str, Any]) -> None:
        for entrada in payload.get("entry") or []:
            for mudanca in entrada.get("changes") or []:
                valor = mudanca.get("value") or {}
                nomes = {
                    c.get("wa_id"): (c.get("profile") or {}).get("name") for c in valor.get("contacts") or []
                }
                for mensagem in valor.get("messages") or []:  # `statuses` (entregue, lida) não interessam
                    await self._tratar(mensagem, nomes)

    def _ja_vista(self, id_mensagem: str | None) -> bool:
        if not id_mensagem:
            return False
        if id_mensagem in self._vistas:
            return True
        self._vistas[id_mensagem] = None
        while len(self._vistas) > 500:
            self._vistas.popitem(last=False)
        return False

    async def _tratar(self, mensagem: dict[str, Any], nomes: dict[str, str | None]) -> None:
        if self._ja_vista(mensagem.get("id")):
            return
        origem = str(mensagem.get("from", ""))
        if not origem.isdecimal():
            return
        telefone = int(origem)
        quem = nomes.get(origem) or origem
        await asyncio.to_thread(_gravar_recebida, telefone)  # o contato falou: a janela de 24 h abre
        tipo = mensagem.get("type")
        botao = None
        if tipo == "interactive":  # clique num botão da mensagem livre
            botao = (mensagem.get("interactive") or {}).get("button_reply", {}).get("id")
        elif tipo == "button":  # clique numa resposta rápida do modelo
            botao = (mensagem.get("button") or {}).get("payload")
        if botao is not None:
            await self._ao_clicar(telefone, quem, botao)
        elif tipo == "text":
            await self._ao_escrever(telefone, quem, ((mensagem.get("text") or {}).get("body") or "").strip())

    async def _ao_clicar(self, telefone: int, quem: str, botao: str) -> None:
        decisao = ler_callback(botao)
        if decisao is None:
            return
        autorizado = await asyncio.to_thread(
            destinos.pode_aprovar, decisao.incidente_id, telefone, self.gerente_padrao, CONTATO_WHATSAPP
        )
        if not autorizado:
            log.warning(
                "aprovação de telefone não autorizado ignorada: %s (incidente %s)",
                telefone,
                decisao.incidente_id,
            )
            await self._responder(telefone, SO_GERENTE_APROVA)
            return
        resposta = replace(decisao, respondido_por=quem)
        try:
            await self.ao_responder(resposta)
        except DecisaoJaRegistrada:
            await self._responder(telefone, DECISAO_JA_REGISTRADA)
        except Exception:
            log.exception("falha ao registrar a resposta %s", resposta)
            await self._responder(telefone, "Não consegui registrar a resposta. Tente pelo painel.")
        else:
            await self._responder(telefone, f"{ECO[resposta.resposta]} por {quem}.")

    async def _pode_perguntar(self, telefone: int) -> bool:
        if telefone in self.numeros_pergunta:
            return True
        return telefone in await asyncio.to_thread(destinos.todos_os_chats_de_gerentes, CONTATO_WHATSAPP)

    async def _ao_escrever(self, telefone: int, quem: str, pergunta: str) -> None:
        """Texto de um gerente = pergunta à bula. Roda em segundo plano (a resposta do LLM demora)."""
        if not pergunta:
            return
        if not await self._pode_perguntar(telefone):
            log.warning("pergunta de número não autorizado ignorada: %s", telefone)
            await self._responder(telefone, NAO_AUTORIZADO)
            return
        if self.ao_perguntar is None:
            await self._responder(telefone, SEM_PERGUNTA)
            return
        if len(pergunta) < 3:
            await self._responder(telefone, AJUDA)
            return
        tarefa = asyncio.get_running_loop().create_task(self._responder_pergunta(telefone, pergunta, quem))
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)

    async def _responder_pergunta(self, telefone: int, pergunta: str, quem: str) -> None:
        await self._responder(telefone, "Consultando as bulas…")
        try:
            texto = await self.ao_perguntar(pergunta, quem)
        except Exception:
            log.exception("falha ao responder a pergunta à bula")
            texto = "Não consegui consultar as bulas agora. Tente de novo em instantes."
        await self._responder(telefone, texto)

    async def parar(self) -> None:
        await self.aguardar()
        await self.http.aclose()
