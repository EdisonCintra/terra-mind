"""Canal WhatsApp (F4, contratos 6, 7 e 20) pelo Twilio.

Mesma interface do Telegram (`NotificationChannel`): os nós do `agents` não mudam. Diferenças:

1. **A resposta chega por webhook HTTPS público**, não por polling. O `agents` expõe `POST /whatsapp/webhook`
   (ver `agents/api.py`) e entrega ao canal. O Twilio manda formulário (`application/x-www-form-urlencoded`) e
   assina com `X-Twilio-Signature` (HMAC-SHA1 da URL mais os parâmetros em ordem, com o Auth Token); sem
   assinatura válida, 403.
2. **Fora da janela de 24 h** desde a última mensagem do contato, o WhatsApp só deixa mandar modelo aprovado
   (Content Template do Twilio, por `ContentSid`). Dentro dela, texto livre e botões. O canal guarda a última
   mensagem recebida de cada telefone (`agente.whatsapp_janelas`) e escolhe a forma; se o Twilio recusar a
   mensagem livre por janela fechada (erro 63016), cai para o modelo.
3. **Os botões voltam sem o plano.** O botão do modelo tem identificador fixo (`SIM`, `OPCOES`, `NAO`): o
   canal grava o SID de cada mensagem de aprovação em `agente.whatsapp_mensagens` e, no clique, acha o plano
   pela mensagem respondida (`OriginalRepliedMessageSid`) ou, na falta dela, pelo plano mais recente ainda
   sem resposta enviado àquele telefone. Sem modelo de botões configurado (ou no Sandbox sem eles), a
   aprovação vai como texto e o gerente responde escrevendo SIM, OPCOES ou NAO: vale o mesmo caminho.

Destino (contrato 20): como no Telegram, o gerente da fazenda do incidente (`erp.gerentes.whatsapp_telefone`);
`WHATSAPP_GERENTE_TELEFONE` é só o gerente padrão. Só o telefone de um gerente da fazenda do incidente aprova.

Mensagem de texto de um gerente (ou de um número em `WHATSAPP_NUMEROS_BULA`) é uma pergunta ao assistente,
com o contato `whatsapp:<telefone>`: dados das fazendas desse gerente e a bula. Número extra só consulta a bula.
Passo a passo do Twilio, do Sandbox à produção: `docs/whatsapp.md`.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import replace

import httpx

from channels import destinos
from channels.base import (
    DECISAO_JA_REGISTRADA,
    ECO,
    RESPOSTAS_VALIDAS,
    ROTULOS,
    AoResponder,
    DecisaoJaRegistrada,
    NotificationChannel,
    Resposta,
    ler_callback,
)
from channels.destinos import CONTATO_WHATSAPP
from erp_mcp.db import conectar

log = logging.getLogger(__name__)

URL_TWILIO = "https://api.twilio.com"

LIMITE_CORPO_BOTOES = 1024  # corpo de mensagem com botões
LIMITE_TEXTO = 1600  # mensagem de texto livre no Twilio
LIMITE_PARAMETRO = 1000  # variável do modelo aprovado
JANELA_H = 24
MARGEM_JANELA_MIN = 5  # trata a janela como fechada um pouco antes, para a mensagem não chegar na borda
ERROS_FORA_DA_JANELA = {63016}  # "freeform message outside the allowed window"

AJUDA = (
    "Pergunte o que quiser sobre a fazenda: OS, incidentes, previsão, estoque, máquinas, perda evitada,\n"
    "melhor janela para aplicar e as bulas.\n"
    "Ex.: Quais OS estão suspensas?\n"
    "Ex.: Vai chover no Talhão 1 amanhã?\n"
    "Ex.: Qual a carência do Brucia no milho?\n"
    "O que vier da bula traz a página. Para responder um plano, use os botões."
)
NAO_AUTORIZADO = "Este número ainda não está liberado para perguntas. Peça ao gerente da fazenda."
SEM_PERGUNTA = "As perguntas ainda não estão ligadas neste canal."
SO_GERENTE_APROVA = "Só o gerente da fazenda deste incidente pode aprovar ou recusar o plano."
PLANO_NAO_ACHADO = "Não achei o plano desta resposta. Responda pelo painel."
COMO_RESPONDER = (
    "Responda *SIM* para aceitar a proposta, *OPCOES* para ver a próxima opção ou *NAO* para recusar."
)

# Texto que vale como decisão (sem acento, minúsculo): o clique no botão chega assim no `Body` e quem
# escreve à mão também usa estas palavras.
DECISOES_POR_TEXTO = {
    "sim": "SIM",
    "aceitar": "SIM",
    "aceitar proposta": "SIM",
    "nao": "NAO",
    "manter": "NAO",
    "manter data": "NAO",
    "manter data original": "NAO",
    "opcoes": "OPCOES",
    "opcao": "OPCOES",
    "ver proxima opcao": "OPCOES",
    "proxima opcao": "OPCOES",
    "proxima": "OPCOES",
}


class WhatsAppErro(Exception):
    """O Twilio recusou o envio. `codigo` é o código de erro dele (ex.: 63016)."""

    def __init__(self, mensagem: str, codigo: int | None = None):
        super().__init__(mensagem)
        self.codigo = codigo


def achatar(texto: str, limite: int = LIMITE_PARAMETRO) -> str:
    """Variável de modelo aprovado não aceita quebra de linha nem 4+ espaços: junta as linhas com " · "."""
    partes = [p.strip() for p in texto.replace("\t", " ").splitlines() if p.strip()]
    plano = " · ".join(partes)
    while "    " in plano:
        plano = plano.replace("    ", "   ")
    return plano if len(plano) <= limite else plano[: limite - 1] + "…"


def cortar(texto: str, limite: int) -> str:
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def decisao_do_texto(texto: str) -> str | None:
    """SIM, NAO ou OPCOES se o texto é só uma dessas respostas (com ou sem acento, asteriscos e pontuação)."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    limpo = " ".join(re.sub(r"[^a-z0-9 ]", " ", sem_acento.casefold()).split())
    return DECISOES_POR_TEXTO.get(limpo)


def assinatura_twilio(auth_token: str, url: str, parametros: Iterable[tuple[str, str]]) -> str:
    """Assinatura que o Twilio calcula: HMAC-SHA1 de `url` + (nome+valor dos parâmetros, em ordem de nome)."""
    texto = url + "".join(f"{k}{v}" for k, v in sorted(parametros))
    return base64.b64encode(hmac.new(auth_token.encode(), texto.encode(), hashlib.sha1).digest()).decode()


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


def _registrar_mensagem(sid: str | None, telefone: int, incidente_id: int, plano_id: int) -> None:
    with conectar() as conn:
        conn.execute(
            "INSERT INTO agente.whatsapp_mensagens (sid, telefone, incidente_id, plano_id)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (sid) DO NOTHING",
            (sid or None, telefone, incidente_id, plano_id),
        )


def _plano_da_resposta(telefone: int, sid_respondida: str | None) -> tuple[int, int] | None:
    """(incidente, plano) a que a resposta do telefone se refere: a mensagem respondida, senão o plano
    mais recente enviado a ele que ainda não tem resposta."""
    with conectar() as conn:
        linha = None
        if sid_respondida:
            linha = conn.execute(
                "SELECT incidente_id, plano_id FROM agente.whatsapp_mensagens"
                " WHERE sid = %s AND telefone = %s",
                (sid_respondida, telefone),
            ).fetchone()
        if linha is None:
            linha = conn.execute(
                "SELECT m.incidente_id, m.plano_id FROM agente.whatsapp_mensagens m"
                " JOIN agente.planos p ON p.id = m.plano_id AND p.incidente_id = m.incidente_id"
                " WHERE m.telefone = %s AND p.respondido_em IS NULL"
                " ORDER BY m.criada_em DESC, m.id DESC LIMIT 1",
                (telefone,),
            ).fetchone()
    return (linha["incidente_id"], linha["plano_id"]) if linha else None


class WhatsAppChannel(NotificationChannel):
    campo_operador = CONTATO_WHATSAPP

    def __init__(
        self,
        ao_responder: AoResponder,
        account_sid: str | None = None,
        auth_token: str | None = None,
        de: str | None = None,
        gerente_telefone: int | None = None,
        numeros_bula: str | None = None,
        content_aprovacao: str | None = None,
        content_aviso: str | None = None,
        webhook_url: str | None = None,
        api_url: str | None = None,
        cliente: httpx.AsyncClient | None = None,
    ):
        env = os.environ.get
        self.ao_responder = ao_responder
        self.account_sid = account_sid or env("TWILIO_ACCOUNT_SID", "")
        self.auth_token = auth_token or env("TWILIO_AUTH_TOKEN", "")
        remetente = de or env("TWILIO_WHATSAPP_FROM", "")
        if not self.account_sid or not self.auth_token or not remetente:
            raise RuntimeError(
                "CHANNEL=whatsapp exige TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN e TWILIO_WHATSAPP_FROM "
                "preenchidos; sem eles use CHANNEL=painel ou CHANNEL=telegram"
            )
        digitos = re.sub(r"\D", "", remetente.removeprefix("whatsapp:"))
        self.de = f"whatsapp:+{digitos}"
        padrao = gerente_telefone if gerente_telefone is not None else env("WHATSAPP_GERENTE_TELEFONE", "")
        self.gerente_padrao = int(padrao) if str(padrao).strip() else None
        extras = numeros_bula if numeros_bula is not None else env("WHATSAPP_NUMEROS_BULA", "")
        self.numeros_pergunta = {
            *([self.gerente_padrao] if self.gerente_padrao is not None else []),
            *(int(n) for n in str(extras).replace(" ", "").split(",") if n),
        }
        # Modelos (Content Templates) são opcionais: sem o de aprovação, ela vai como texto e o gerente
        # responde SIM, OPCOES ou NAO; sem os modelos, só dá para falar dentro da janela de 24 h.
        self.content_aprovacao = content_aprovacao or env("TWILIO_CONTENT_SID_APROVACAO", "")
        self.content_aviso = content_aviso or env("TWILIO_CONTENT_SID_AVISO", "")
        # URL que o Twilio chama (a do console). Vazia: o `agents` monta a URL pelos cabeçalhos da requisição.
        self.webhook_url = webhook_url if webhook_url is not None else env("TWILIO_WEBHOOK_URL", "")
        base = (api_url or env("TWILIO_API_URL", URL_TWILIO)).rstrip("/")
        self.url = f"{base}/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        self.http = cliente or httpx.AsyncClient(timeout=20)
        self._vistas: OrderedDict[str, None] = OrderedDict()  # o Twilio reenvia o webhook se demorar
        self._tarefas: set[asyncio.Task] = set()

    # --- envio ------------------------------------------------------------------------------
    @staticmethod
    def _para(telefone: int) -> str:
        return f"whatsapp:+{telefone}"

    async def _post(self, dados: dict[str, str]) -> str:
        """Manda a mensagem e devolve o SID dela. `WhatsAppErro` se o Twilio recusar."""
        resposta = await self.http.post(
            self.url, data={"From": self.de, **dados}, auth=(self.account_sid, self.auth_token)
        )
        if resposta.is_success:
            try:
                return str(resposta.json().get("sid") or "")
            except ValueError:
                return ""
        try:
            erro = resposta.json()
        except ValueError:
            erro = {}
        raise WhatsAppErro(
            f"WhatsApp recusou o envio (HTTP {resposta.status_code}): "
            f"{erro.get('message', resposta.text[:200])}",
            erro.get("code"),
        )

    async def _texto(self, telefone: int, texto: str) -> str:
        return await self._post({"To": self._para(telefone), "Body": cortar(texto, LIMITE_TEXTO)})

    async def _modelo(self, telefone: int, content_sid: str, texto: str, nome: str) -> str:
        """Modelo aprovado: `texto` é a variável {{1}}."""
        if not content_sid:
            raise WhatsAppErro(
                f"{telefone} está fora da janela de 24 h e não há modelo configurado ({nome}). "
                "Peça para o contato mandar uma mensagem ao número do TerraMind, ou crie o modelo "
                "(docs/whatsapp.md)."
            )
        variaveis = json.dumps({"1": achatar(texto)}, ensure_ascii=False)
        return await self._post(
            {"To": self._para(telefone), "ContentSid": content_sid, "ContentVariables": variaveis}
        )

    async def _lembrar(self, sid: str, telefone: int, incidente_id: int, plano_id: int) -> None:
        """Guarda de que plano é a mensagem, para achar o plano quando o botão voltar sem ele."""
        try:
            await asyncio.to_thread(_registrar_mensagem, sid, telefone, incidente_id, plano_id)
        except Exception:  # noqa: BLE001 — não derruba o envio
            log.exception("não consegui guardar a mensagem de aprovação %s", sid)

    async def _botoes(self, telefone: int, incidente_id: int, plano_id: int, texto: str) -> None:
        """Aprovação na janela: modelo com 3 botões se houver; senão texto pedindo SIM, OPCOES ou NAO."""
        if self.content_aprovacao:
            corpo = texto
            if len(texto) > LIMITE_CORPO_BOTOES:
                await self._texto(telefone, texto)
                corpo = f"Plano {plano_id} (incidente {incidente_id}): escolha uma opção."
            variaveis = json.dumps({"1": cortar(corpo, LIMITE_CORPO_BOTOES)}, ensure_ascii=False)
            sid = await self._post(
                {
                    "To": self._para(telefone),
                    "ContentSid": self.content_aprovacao,
                    "ContentVariables": variaveis,
                }
            )
        else:
            sid = await self._texto(telefone, f"{texto}\n\n{COMO_RESPONDER}")
        await self._lembrar(sid, telefone, incidente_id, plano_id)

    async def _livre_ou_modelo(self, telefone: int, livre, modelo) -> None:
        """Dentro da janela, a mensagem livre; fora dela (ou se o Twilio recusar por janela), o modelo."""
        if await asyncio.to_thread(_janela_aberta, telefone):
            try:
                await livre()
                return
            except WhatsAppErro as e:
                if e.codigo not in ERROS_FORA_DA_JANELA:
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
                lambda: self._modelo(telefone, self.content_aviso, texto, "TWILIO_CONTENT_SID_AVISO"),
            )

        await self._para_todos(await self._gerentes(incidente_id), enviar)

    async def pedir_aprovacao(self, incidente_id: int, plano_id: int, texto: str) -> None:
        async def fora_da_janela(telefone: int) -> None:
            sid = await self._modelo(telefone, self.content_aprovacao, texto, "TWILIO_CONTENT_SID_APROVACAO")
            await self._lembrar(sid, telefone, incidente_id, plano_id)

        async def enviar(telefone: int) -> None:
            await self._livre_ou_modelo(
                telefone,
                lambda: self._botoes(telefone, incidente_id, plano_id, texto),
                lambda: fora_da_janela(telefone),
            )

        await self._para_todos(await self._gerentes(incidente_id), enviar)

    async def notificar_operador(self, chat_id: int, texto: str) -> None:
        """`chat_id` é o telefone do operador (`erp.operadores.whatsapp_telefone`)."""
        await self._livre_ou_modelo(
            chat_id,
            lambda: self._texto(chat_id, texto),
            lambda: self._modelo(chat_id, self.content_aviso, texto, "TWILIO_CONTENT_SID_AVISO"),
        )

    async def _responder(self, telefone: int, texto: str) -> None:
        """Resposta a uma mensagem que o contato acabou de mandar (janela aberta): nunca derruba o webhook."""
        try:
            await self._texto(telefone, texto)
        except Exception:  # noqa: BLE001
            log.exception("falha ao responder no WhatsApp para %s", telefone)

    # --- webhook ----------------------------------------------------------------------------
    def url_assinada(self, url_da_requisicao: str) -> str:
        """URL que entra na conta da assinatura: a configurada (`TWILIO_WEBHOOK_URL`) ou a da requisição."""
        return self.webhook_url or url_da_requisicao

    def assinatura_valida(
        self, url: str, parametros: Iterable[tuple[str, str]], cabecalho: str | None
    ) -> bool:
        """Confere `X-Twilio-Signature`; sem Auth Token ou sem cabeçalho, nada vale."""
        if not self.auth_token or not cabecalho:
            return False
        return hmac.compare_digest(assinatura_twilio(self.auth_token, url, parametros), cabecalho.strip())

    def agendar(self, dados: dict[str, str]) -> None:
        """Processa em segundo plano: o webhook responde 200 logo, ou o Twilio reenvia a mensagem."""
        tarefa = asyncio.get_running_loop().create_task(self._processar_seguro(dados))
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)

    async def aguardar(self) -> None:
        """Espera o que está em segundo plano (desligamento e testes)."""
        while self._tarefas:
            await asyncio.gather(*list(self._tarefas), return_exceptions=True)

    async def _processar_seguro(self, dados: dict[str, str]) -> None:
        try:
            await self.processar(dados)
        except Exception:  # noqa: BLE001
            log.exception("falha ao processar o webhook do WhatsApp")

    def _ja_vista(self, id_mensagem: str | None) -> bool:
        if not id_mensagem:
            return False
        if id_mensagem in self._vistas:
            return True
        self._vistas[id_mensagem] = None
        while len(self._vistas) > 500:
            self._vistas.popitem(last=False)
        return False

    async def processar(self, dados: dict[str, str]) -> None:
        """Uma mensagem recebida (campos do webhook do Twilio: From, Body, ButtonPayload, MessageSid...)."""
        if self._ja_vista(dados.get("MessageSid") or dados.get("SmsMessageSid")):
            return
        origem = re.sub(r"\D", "", (dados.get("From") or "").removeprefix("whatsapp:"))
        if not origem:
            return
        telefone = int(origem)
        quem = (dados.get("ProfileName") or "").strip() or origem
        await asyncio.to_thread(_gravar_recebida, telefone)  # o contato falou: a janela de 24 h abre
        corpo = (dados.get("Body") or "").strip()
        respondida = dados.get("OriginalRepliedMessageSid")
        botao = (dados.get("ButtonPayload") or "").strip()
        if botao:  # clique num botão: se não achar o plano, avisa
            decisao = await self._decisao(telefone, botao, respondida)
            if decisao is None:
                await self._responder(telefone, PLANO_NAO_ACHADO)
            else:
                await self._ao_clicar(telefone, quem, decisao)
            return
        escrita = decisao_do_texto(corpo)
        if escrita:  # "SIM", "não"...: decisão só se há plano esperando; senão é texto comum
            decisao = await self._decisao(telefone, escrita, respondida)
            if decisao is not None:
                await self._ao_clicar(telefone, quem, decisao)
                return
        if corpo:
            await self._ao_escrever(telefone, quem, corpo)

    async def _decisao(self, telefone: int, botao: str, respondida: str | None) -> Resposta | None:
        """`Resposta` (sem `respondido_por`) do botão ou do texto: com o plano no id (`r:inc:plano:R`)
        ou só com SIM, OPCOES ou NAO, caso em que o plano sai de `agente.whatsapp_mensagens`."""
        completa = ler_callback(botao)
        if completa is not None:
            return completa
        if botao not in RESPOSTAS_VALIDAS:
            return None
        alvo = await asyncio.to_thread(_plano_da_resposta, telefone, respondida)
        return Resposta(alvo[0], alvo[1], botao, "") if alvo else None  # type: ignore[arg-type]

    async def _ao_clicar(self, telefone: int, quem: str, decisao: Resposta) -> None:
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
        """Texto de um gerente = pergunta ao assistente. Roda em segundo plano (a resposta do LLM demora)."""
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
        await self._responder(telefone, "Consultando…")
        try:
            texto = await self.ao_perguntar(pergunta, quem, contato=f"whatsapp:{telefone}")
        except Exception:
            log.exception("falha ao responder a pergunta do chat")
            texto = "Não consegui consultar agora. Tente de novo em instantes."
        await self._responder(telefone, texto)

    async def parar(self) -> None:
        await self.aguardar()
        await self.http.aclose()


__all__ = ["ROTULOS", "WhatsAppChannel", "WhatsAppErro", "achatar", "assinatura_twilio", "decisao_do_texto"]
