"""G2 e G3: a semana de cada talhão para um produto e a avaliação de uma janela antes de criar a OS.

Fora do incidente não há Avaliador nem snapshot: os limites vêm de `conhecimento.limites_produto`
(contrato 26, com a referência genérica para o que a bula não traz) e a previsão, do cliente de clima.
Nenhum LLM participa: horas boas, motivos e sugestões saem de `agents.rules`.
"""

import asyncio
from datetime import date, datetime, timedelta

from pydantic import AwareDatetime, BaseModel, Field

from agents.clientes.clima import ClimaIndisponivel
from agents.clientes.erp import ErpIndisponivel
from agents.deps import Dependencias
from agents.modelos import LimiteProduto, OsEmRisco
from agents.nodes.contexto import TIPO_MAQUINA_POR_OPERACAO
from agents.rules.agenda import HORIZONTE, DiaAgenda, agenda_do_talhao, reprovacao
from agents.rules.janelas import FUSO, blocos, janelas_climaticas, motivo_horario
from agents.rules.limites import com_dado, fonte_do_limite, limites_efetivos
from agents.rules.mensagem import formatar_data_longa
from agents.rules.validador import maquinas_livres

MAX_SUGESTOES = 3
OPERACAO = "pulverizacao"  # o ERP só tem defensivos (contrato 15)


class AgendaIndisponivel(Exception):
    """ERP ou previsão fora do ar: a rota responde 503 com o motivo."""


class NaoEncontrado(Exception):
    """Talhão ou insumo que não existe: a rota responde 404."""


class TalhaoAgenda(BaseModel):
    talhao_id: int
    talhao: str
    cultura: str | None = None
    dias: list[DiaAgenda]


class Agenda(BaseModel):
    insumo_id: int
    produto: str
    duracao_horas: float
    inicio: date
    limites: list[LimiteProduto]
    bula_lida: bool = Field(
        description="false = só referência genérica; leia a bula em POST /limites/extrair"
    )
    fonte_previsao: str
    talhoes: list[TalhaoAgenda]
    erros: list[str] = []


class PedidoJanela(BaseModel):
    talhao_id: int
    insumo_id: int
    inicio: AwareDatetime
    duracao_horas: float = Field(8, gt=0, le=24)


class Citacao(BaseModel):
    trecho: str
    fonte_doc: str
    pagina: int


class Sugestao(BaseModel):
    inicio: AwareDatetime
    maquina_id: int
    maquina: str | None = None
    quando: str  # "quarta, 18/11 às 06:00"


class AvaliacaoJanela(BaseModel):
    """Contrato 24. `citacao` só vem quando o limite desrespeitado saiu da bula."""

    serve: bool
    motivo: str | None = None
    citacao: Citacao | None = None
    fonte_limite: str | None = None  # "bula, p. 7" ou "referência genérica"
    sugestoes: list[Sugestao] = []
    fonte_previsao: str


class ServicoAgenda:
    def __init__(self, deps: Dependencias):
        self.deps = deps

    async def _limites(self, produto: str) -> tuple[list[LimiteProduto], bool]:
        da_bula = await self.deps.repo.limites_do_produto(produto)
        return limites_efetivos(da_bula), bool(da_bula)

    async def _insumo(self, insumo_id: int) -> dict:
        try:
            return await self.deps.erp.consultar_insumo(insumo_id)
        except ErpIndisponivel as e:
            if "não existe" in str(e):
                raise NaoEncontrado(f"insumo {insumo_id} não existe") from e
            raise AgendaIndisponivel(str(e)) from e

    async def _previsao(self, talhao: dict, dias: int):
        if self.deps.clima is None:
            raise AgendaIndisponivel("previsão não configurada")
        if talhao.get("lat") is None or talhao.get("lon") is None:
            raise AgendaIndisponivel(f"talhão {talhao.get('id')} sem coordenadas")
        try:
            return await self.deps.clima.previsao(float(talhao["lat"]), float(talhao["lon"]), dias=dias)
        except ClimaIndisponivel as e:
            raise AgendaIndisponivel(f"previsão indisponível: {e}") from e

    async def semana(
        self,
        insumo_id: int,
        inicio: date | None = None,
        dias: int = 7,
        duracao_horas: float = 8,
        talhao_id: int | None = None,
        fazenda_id: int | None = None,
    ) -> Agenda:
        """Por talhão e por dia, as faixas boas e ruins para aplicar o produto, com o motivo.

        Contrato 20: o insumo é de uma fazenda e a agenda só traz os talhões dela. Com `fazenda_id`
        de outra fazenda, o insumo "não existe" para quem pergunta.
        """
        agora = self.deps.relogio()
        inicio = inicio or agora.astimezone(FUSO).date()
        insumo = await self._insumo(insumo_id)
        dona = insumo.get("fazenda_id")
        if fazenda_id is not None and dona is not None and dona != fazenda_id:
            raise NaoEncontrado(f"insumo {insumo_id} não existe")
        try:
            talhoes = await self.deps.erp.listar_talhoes(fazenda_id if fazenda_id is not None else dona)
        except ErpIndisponivel as e:
            raise AgendaIndisponivel(str(e)) from e
        if talhao_id is not None:
            talhoes = [t for t in talhoes if t["id"] == talhao_id]
            if not talhoes:
                raise NaoEncontrado(f"talhão {talhao_id} não existe")
        limites, bula_lida = await self._limites(insumo["produto"])
        # Dias a pedir à previsão: do dia de hoje até o fim do período, mais a folga do limite mais longo.
        ate = (inicio - agora.astimezone(FUSO).date()).days + dias + 2
        resultados = await asyncio.gather(*(self._previsao(t, ate) for t in talhoes), return_exceptions=True)
        agenda = Agenda(
            insumo_id=insumo_id,
            produto=insumo["produto"],
            duracao_horas=duracao_horas,
            inicio=inicio,
            limites=limites,
            bula_lida=bula_lida,
            fonte_previsao="",
            talhoes=[],
        )
        for talhao, previsao in zip(talhoes, resultados, strict=True):
            if isinstance(previsao, AgendaIndisponivel):
                agenda.erros.append(f"{talhao['nome']}: {previsao}")
                continue
            if isinstance(previsao, BaseException):
                raise previsao
            agenda.fonte_previsao = previsao.fonte
            agenda.talhoes.append(
                TalhaoAgenda(
                    talhao_id=talhao["id"],
                    talhao=talhao["nome"],
                    cultura=talhao.get("cultura"),
                    dias=agenda_do_talhao(
                        previsao.horas, com_dado(limites, previsao.horas), duracao_horas, agora, inicio, dias
                    ),
                )
            )
        if talhoes and not agenda.talhoes:
            raise AgendaIndisponivel("; ".join(agenda.erros))
        return agenda

    async def avaliar(self, pedido: PedidoJanela) -> AvaliacaoJanela:
        """A janela pedida serve? Se não, por quê (com a página da bula) e quais as próximas boas."""
        agora = self.deps.relogio()
        insumo = await self._insumo(pedido.insumo_id)
        try:
            talhao = await self.deps.erp.consultar_talhao(pedido.talhao_id)
        except ErpIndisponivel as e:
            if "não existe" in str(e):
                raise NaoEncontrado(f"talhão {pedido.talhao_id} não existe") from e
            raise AgendaIndisponivel(str(e)) from e
        limites, _ = await self._limites(insumo["produto"])
        previsao = await self._previsao(talhao, dias=7)
        limites = com_dado(limites, previsao.horas)
        os = OsEmRisco(
            os_id=0,
            talhao_id=pedido.talhao_id,
            talhao=talhao.get("nome") or f"Talhão {pedido.talhao_id}",
            area_ha=float(talhao.get("area_ha") or 0),
            tipo_operacao=OPERACAO,
            inicio_previsto=pedido.inicio,
            duracao_horas=pedido.duracao_horas,
            tipo_maquina=TIPO_MAQUINA_POR_OPERACAO[OPERACAO],
        )
        resultado = AvaliacaoJanela(serve=True, fonte_previsao=previsao.fonte)
        motivo = motivo_horario(pedido.inicio, agora)
        reprovado = (
            None if motivo else reprovacao(pedido.inicio, previsao.horas, limites, pedido.duracao_horas)
        )
        try:
            if motivo:
                resultado.serve, resultado.motivo = False, motivo
            elif reprovado:
                texto, limite = reprovado
                resultado.serve = False
                resultado.fonte_limite = None if texto == HORIZONTE else fonte_do_limite(limite)
                resultado.motivo = self._motivo(pedido, texto, limite)
                if texto != HORIZONTE and not limite.generico:
                    resultado.citacao = Citacao(
                        trecho=limite.trecho, fonte_doc=limite.fonte_doc, pagina=limite.pagina
                    )
            elif not await maquinas_livres(self.deps.erp, pedido.inicio, os):
                resultado.serve = False
                resultado.motivo = f"nenhum {os.tipo_maquina} livre nesse horário"
            if not resultado.serve:
                resultado.sugestoes = await self._sugestoes(pedido, previsao.horas, limites, os, agora)
        except ErpIndisponivel as e:
            raise AgendaIndisponivel(str(e)) from e
        return resultado

    @staticmethod
    def _motivo(pedido: PedidoJanela, texto: str, limite: LimiteProduto) -> str:
        """ "quarta, 18/11 às 14:00: chuva prevista de 12.0 mm acima do limite de 1 mm; a bula pede 4 h
        sem chuva após a aplicação (bula, p. 7)"."""
        quando = formatar_data_longa(pedido.inicio)
        if texto == HORIZONTE:
            return f"{quando}: a previsão ainda não alcança esse horário"
        regra = ""
        if limite.parametro == "chuva" and limite.janela_h:
            regra = f"; o limite vale na aplicação e nas {limite.janela_h:g} h seguintes"
        return f"{quando}: {texto}{regra} ({fonte_do_limite(limite)})"

    async def _sugestoes(self, pedido: PedidoJanela, horas, limites, os: OsEmRisco, agora: datetime) -> list:
        """A primeira hora com máquina livre de cada uma das próximas janelas boas, a partir do pedido."""
        validos = janelas_climaticas(horas, limites, pedido.duracao_horas, agora)
        sugestoes: list[Sugestao] = []
        for bloco in blocos([t for t in validos if t > pedido.inicio]):
            for t in bloco:
                livres = await self.deps.erp.disponibilidade_maquinas(
                    t, t + timedelta(hours=pedido.duracao_horas), os.tipo_maquina, pedido.talhao_id
                )
                if livres:
                    maquina = min(livres, key=lambda m: m["id"])
                    sugestoes.append(
                        Sugestao(
                            inicio=t,
                            maquina_id=maquina["id"],
                            maquina=maquina.get("nome"),
                            quando=formatar_data_longa(t),
                        )
                    )
                    break
            if len(sugestoes) >= MAX_SUGESTOES:
                break
        return sugestoes
