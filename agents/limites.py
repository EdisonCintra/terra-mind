"""D6 / contrato 26: lê uma vez os limites de aplicação de uma bula e guarda com trecho e página.

A agenda (G2) e a avaliação de janela (G3) rodam antes de qualquer incidente, sem o Avaliador. Este
agente faz a leitura uma vez por produto; depois disso a agenda não chama LLM.

Mesma garantia do Avaliador e do Pergunte à bula, toda em código:
- a citação precisa ser literal de um trecho que a busca devolveu, com a mesma fonte e página
  (`agents.rules.citacao.citacao_confere`);
- o número do limite (ou das horas da janela) precisa aparecer no trecho citado;
- o valor precisa caber na faixa do parâmetro (`LimiteProduto`).
Limite que falha em qualquer conferência é descartado. Sem nenhum válido, nada é gravado e a agenda
segue na referência genérica.
"""

import logging
import re
import uuid

from google.adk import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from agents.agentes._comum import chave_falha, criar_agente
from agents.agentes.pergunte_bula.agent import CHAVE_VISTOS, criar_buscar_na_bula
from agents.deps import Dependencias
from agents.modelos import CitacaoBula, LimiteProduto, Parametro, Sentido
from agents.rules.citacao import citacao_confere, normalizar

logger = logging.getLogger(__name__)

APP_NAME = "extrator_limites"
USER_ID = "terramind"
CHAVE_SAIDA = "limites_extraidos"
SAIDA_VAZIA = '{"limites": []}'
POR_EXTENSO = {
    0: "zero", 1: "uma", 2: "duas", 3: "tres", 4: "quatro", 5: "cinco", 6: "seis", 8: "oito",
    10: "dez", 12: "doze", 24: "vinte e quatro", 48: "quarenta e oito",
}  # fmt: skip


class LimiteExtraido(BaseModel):
    parametro: Parametro
    sentido: Sentido = Field(
        description='"maximo" (não pode passar de) ou "minimo" (não pode ficar abaixo de)'
    )
    limite: float = Field(description="Número do limite na unidade do TerraMind (mm, km/h, %, °C)")
    unidade: str | None = None
    janela_h: float | None = Field(
        None, description="Horas após a aplicação em que o limite vale (ex.: 4 para '4 h sem chuva')"
    )
    trecho: str = Field(description="Texto LITERAL do trecho devolvido por buscar_na_bula")
    fonte_doc: str
    pagina: int


class LimitesExtraidos(BaseModel):
    limites: list[LimiteExtraido] = []


class ResultadoExtracao(BaseModel):
    produto: str
    limites: list[LimiteProduto] = []
    descartados: list[str] = Field([], description="Por que cada limite proposto pelo LLM não foi aceito")
    erro: str | None = None


def instrucao(rc: ReadonlyContext) -> str:
    produto = rc.state.get("produto", "")
    return f"""Você lê a bula do produto "{produto}" e extrai os limites de CLIMA para a aplicação.
Use só o que buscar_na_bula devolver. Nunca responda de memória e nunca invente número ou página.

Como trabalhar:
1. Chame buscar_na_bula com produto="{produto}" e estas consultas, uma por vez: "condições
   meteorológicas ou climáticas para a aplicação", "chuva após a aplicação", "velocidade do vento",
   "temperatura e umidade relativa do ar", "inversão térmica".
2. Para cada limite que um trecho traz de forma explícita, devolva um item:
   - chuva: `parametro="chuva"`, `sentido="maximo"`, `limite=0` e `janela_h` = horas sem chuva depois
     da aplicação que o trecho pede (ex.: "chuvas dentro de quatro (4) horas" → janela_h=4);
   - vento: km/h. Uma faixa ("entre 3 e 10 km/h") vira dois itens: máximo 10 e mínimo 3. Se o trecho
     der m/s, converta (× 3,6) e diga na unidade "km/h";
   - temperatura (°C, quase sempre máximo), umidade (%, quase sempre mínimo), rajada (km/h),
     delta_t (°C);
   - inversão térmica: `parametro="inversao"`, `sentido="maximo"`, `limite=0` quando o trecho mandar
     evitar a aplicação com inversão.
3. Em `trecho`, copie o texto LITERAL do trecho (pode cortar com "..."), e em `fonte_doc` e `pagina`
   repita exatamente o que a ferramenta devolveu. O número do limite precisa estar nesse trecho.
4. Não devolva dose, intervalo de segurança, reentrada nem recomendações sem número.
5. Se a bula não trouxer nenhum limite de clima, devolva `limites` vazio."""


def criar(deps: Dependencias) -> Agent:
    return criar_agente(
        deps,
        "avaliador",  # mesma tarefa do Avaliador (ler limite na bula): usa o mesmo modelo
        name="extrator_limites",
        description="Extrai da bula os limites de clima para a aplicação, com trecho e página.",
        instruction=instrucao,
        tools=[criar_buscar_na_bula(deps)],
        output_schema=LimitesExtraidos,
        output_key=CHAVE_SAIDA,
        saida_vazia=SAIDA_VAZIA,
    )


def _numero_no_trecho(numero: float, trecho: str) -> bool:
    """O número aparece no trecho, em algarismos ("4", "4,0") ou por extenso ("quatro")?"""
    texto = normalizar(trecho)
    inteiro = int(numero) if float(numero).is_integer() else None
    formas = {f"{numero:g}", f"{numero:g}".replace(".", ",")}
    if any(re.search(rf"(?<![\d,.]){re.escape(f)}(?![\d]|[.,]\d)", texto) for f in formas):
        return True
    return inteiro in POR_EXTENSO and re.search(rf"\b{POR_EXTENSO[inteiro]}\b", texto) is not None


def conferir(extraidos: LimitesExtraidos, vistos: list[dict]) -> tuple[list[LimiteProduto], list[str]]:
    """Separa os limites que passam nas conferências dos descartados (com o motivo)."""
    aceitos: dict[tuple[str, str], LimiteProduto] = {}
    descartados: list[str] = []
    for e in extraidos.limites:
        rotulo = f"{e.parametro} {e.sentido} {e.limite:g}"
        citacao = CitacaoBula(trecho=e.trecho, fonte_doc=e.fonte_doc, pagina=e.pagina)
        if citacao_confere(citacao, vistos) is None:
            descartados.append(f"{rotulo}: o trecho citado não veio da busca (p. {e.pagina})")
            continue
        # Chuva e inversão têm limite 0 ("sem chuva"): o número que identifica a regra é o da janela.
        numero = e.janela_h if e.limite == 0 else e.limite
        if numero is not None and not _numero_no_trecho(numero, e.trecho):
            descartados.append(f"{rotulo}: o número {numero:g} não aparece no trecho citado")
            continue
        try:
            limite = LimiteProduto.model_validate(e.model_dump())
        except ValidationError as erro:
            descartados.append(f"{rotulo}: {erro.errors()[0]['msg']}")
            continue
        aceitos.setdefault(limite.chave, limite)
    return list(aceitos.values()), descartados


class ExtratorLimites:
    def __init__(self, deps: Dependencias):
        self.deps = deps
        self.sessoes = InMemorySessionService()
        self.runner = Runner(app_name=APP_NAME, agent=criar(deps), session_service=self.sessoes)

    async def extrair(self, produto: str) -> ResultadoExtracao:
        """Lê a bula, confere e grava. Nunca levanta: a tela da agenda mostra `erro`."""
        try:
            estado = await self._rodar(produto)
        except Exception as e:  # noqa: BLE001 - falha do LLM não derruba a tela
            logger.exception("extração de limites falhou para %s", produto)
            return ResultadoExtracao(produto=produto, erro=f"{type(e).__name__}: {e}")
        falha = estado.get(chave_falha("extrator_limites"))
        if falha:
            return ResultadoExtracao(produto=produto, erro=falha)
        bruta = estado.get(CHAVE_SAIDA) or SAIDA_VAZIA
        try:
            extraidos = (
                LimitesExtraidos.model_validate_json(bruta)
                if isinstance(bruta, str)
                else LimitesExtraidos.model_validate(bruta)
            )
        except ValidationError as e:
            return ResultadoExtracao(produto=produto, erro=f"saída fora do schema: {e.errors()[0]['msg']}")
        limites, descartados = conferir(extraidos, estado.get(CHAVE_VISTOS) or [])
        if limites:
            await self.deps.repo.salvar_limites(produto, limites)
        await self._auditar(produto, limites, descartados)
        return ResultadoExtracao(produto=produto, limites=limites, descartados=descartados)

    async def _rodar(self, produto: str) -> dict:
        sid = f"limites-{uuid.uuid4().hex}"
        await self.sessoes.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=sid, state={"produto": produto, CHAVE_VISTOS: []}
        )
        mensagem = types.Content(role="user", parts=[types.Part(text=f"Extraia os limites de {produto}.")])
        try:
            async for evento in self.runner.run_async(user_id=USER_ID, session_id=sid, new_message=mensagem):
                if evento.error_code:
                    logger.error("extrator de limites: %s %s", evento.error_code, evento.error_message)
            sessao = await self.sessoes.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=sid)
            return dict(sessao.state) if sessao else {}
        finally:
            await self.sessoes.delete_session(app_name=APP_NAME, user_id=USER_ID, session_id=sid)

    async def _auditar(self, produto: str, limites: list[LimiteProduto], descartados: list[str]) -> None:
        try:
            await self.deps.repo.auditar(
                None,
                "extrator_limites",
                "extrair_limites",
                antes={"produto": produto},
                depois={"limites": [limite.model_dump(mode="json") for limite in limites]},
                justificativa="; ".join(descartados) or None,
            )
        except Exception:  # noqa: BLE001
            logger.exception("falha ao auditar a extração de limites")
