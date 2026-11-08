"""G4: roda o agente Pergunte à bula fora do workflow do incidente e confere a resposta em código."""

import logging
import uuid

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import ValidationError

from agents.agentes._comum import chave_falha
from agents.agentes.pergunte_bula.agent import CHAVE_RESPOSTA, CHAVE_VISTOS, criar
from agents.deps import Dependencias
from agents.modelos import RespostaBula, ResultadoBula
from agents.rules.citacao import NAO_ENCONTRADO, conferir

logger = logging.getLogger(__name__)

APP_NAME = "pergunte_bula"
USER_ID = "terramind"
FALHA = "Não consegui consultar as bulas agora. Tente de novo em instantes."
TENTATIVAS = 2


LIMITE_TRECHO_CANAL = 280


def texto_para_canal(r: ResultadoBula) -> str:
    """Resposta para o celular: o texto, e para cada citação a bula, a página e o trecho literal."""
    if not r.encontrou:
        return r.resposta
    linhas = [r.resposta]
    for c in r.citacoes:
        trecho = " ".join(c.trecho.split())
        if len(trecho) > LIMITE_TRECHO_CANAL:
            trecho = trecho[: LIMITE_TRECHO_CANAL - 1].rstrip() + "…"
        linhas += ["", f"Fonte: bula {c.produto or c.fonte_doc}, p. {c.pagina}", f"“{trecho}”"]
    return "\n".join(linhas)


def _resposta_do_estado(estado: dict) -> RespostaBula | None:
    bruta = estado.get(CHAVE_RESPOSTA)
    if bruta is None or bruta == "":
        return None
    try:
        if isinstance(bruta, str):
            return RespostaBula.model_validate_json(bruta)
        return RespostaBula.model_validate(bruta)
    except ValidationError:
        logger.warning("resposta da bula fora do schema: %s", bruta)
        return None


class PergunteBula:
    """Uma sessão em memória por pergunta: não há histórico entre perguntas (a bula é a única fonte)."""

    def __init__(self, deps: Dependencias):
        self.deps = deps
        self.sessoes = InMemorySessionService()
        self.runner = Runner(app_name=APP_NAME, agent=criar(deps), session_service=self.sessoes)

    async def responder_no_canal(self, pergunta: str, quem: str) -> str:
        """`ao_perguntar` dos canais (Telegram): pergunta livre, sem produto escolhido."""
        return texto_para_canal(await self.perguntar(pergunta, None, quem=quem))

    async def perguntar(
        self, pergunta: str, produto: str | None = None, quem: str = "painel"
    ) -> ResultadoBula:
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                estado, erro = await self._rodar(pergunta, produto)
            except Exception:  # noqa: BLE001 - a tela de chat não pode cair por causa do LLM
                logger.exception("pergunte à bula falhou")
                return ResultadoBula(encontrou=False, resposta=FALHA, erro="falha_interna")
            falha = estado.get(chave_falha("pergunte_bula"))
            if falha:
                return ResultadoBula(encontrou=False, resposta=FALHA, erro=falha)
            resposta = _resposta_do_estado(estado)
            # Uma execução normal sempre termina com a resposta no schema. Sem ela, o modelo parou no
            # meio (o gpt às vezes devolve conteúdo vazio, MODEL_RETURNED_NO_CONTENT): tenta de novo do
            # zero e, se persistir, avisa a falha em vez de dizer que a bula não traz o assunto.
            if resposta is None:
                if tentativa < TENTATIVAS:
                    logger.warning("pergunte à bula sem resposta (%s); tentando de novo", erro)
                    continue
                return ResultadoBula(encontrou=False, resposta=FALHA, erro=erro or "modelo_sem_resposta")
            resultado = conferir(resposta, estado.get(CHAVE_VISTOS) or [])
            await self._auditar(pergunta, produto, quem, resultado)
            return resultado
        raise AssertionError("inalcançável")

    async def _rodar(self, pergunta: str, produto: str | None) -> tuple[dict, str | None]:
        """Uma execução numa sessão nova: (estado final, último erro reportado pelo ADK)."""
        sid = f"bula-{uuid.uuid4().hex}"
        await self.sessoes.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=sid,
            state={"pergunta": pergunta, "produto": produto, CHAVE_VISTOS: []},
        )
        mensagem = types.Content(role="user", parts=[types.Part(text=pergunta)])
        erro = None
        try:
            async for evento in self.runner.run_async(user_id=USER_ID, session_id=sid, new_message=mensagem):
                if evento.error_code:
                    erro = evento.error_code
                    logger.error("pergunte à bula: %s %s", evento.error_code, evento.error_message)
            sessao = await self.sessoes.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=sid)
            return (dict(sessao.state) if sessao else {}), erro
        finally:
            await self.sessoes.delete_session(app_name=APP_NAME, user_id=USER_ID, session_id=sid)

    async def _auditar(self, pergunta: str, produto: str | None, quem: str, resultado: ResultadoBula) -> None:
        try:
            await self.deps.repo.auditar(
                None,
                "pergunte_bula",
                "responder_pergunta",
                antes={"pergunta": pergunta, "produto": produto, "por": quem},
                depois=resultado.model_dump(mode="json"),
                justificativa=None if resultado.encontrou else NAO_ENCONTRADO,
            )
        except Exception:  # noqa: BLE001 - a auditoria não pode derrubar a resposta
            logger.exception("falha ao auditar a pergunta à bula")
