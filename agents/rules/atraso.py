"""G5: quanto custa o gerente demorar a decidir. Tudo em código, como a perda evitada.

A OS em risco já está suspensa; o que custa é a demora. A janela proposta pertence a um bloco de
inícios válidos seguidos (`janelas.blocos`) e "vence" quando o último início do bloco fica a menos de
`ANTECEDENCIA` de agora. Perdida, a aplicação vai para a próxima janela boa, e o atraso custa:

    valor da aplicação (área × dose × preço) × fator de atraso por dia × dias até a próxima janela

O fator por dia vem do conhecimento (`condicao="atraso"`, entrega do E7). Enquanto ele não existir,
vale a estimativa declarada em `Settings.fator_atraso_dia`, sempre marcada como estimativa.
A próxima janela considera só o clima: a máquina é conferida quando o plano novo for proposto.
"""

import logging
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from agents.clientes.conhecimento import ConhecimentoCliente, ConhecimentoIndisponivel
from agents.config import Settings
from agents.modelos import CustoAtraso, OsEmRisco, PlanoProposto
from agents.rules.impacto import CENTAVO, formatar_reais
from agents.rules.janelas import ANTECEDENCIA, blocos, janelas_climaticas
from agents.rules.mensagem import formatar_data_longa
from agents.rules.validador import ContextoPlano

logger = logging.getLogger(__name__)

CONDICAO_ATRASO = "atraso"


def valor_aplicacao(os: OsEmRisco) -> float | None:
    if os.dose_por_ha is None or os.preco_unitario is None:
        return None
    valor = Decimal(str(os.area_ha)) * Decimal(str(os.dose_por_ha)) * Decimal(str(os.preco_unitario))
    return float(valor.quantize(CENTAVO, rounding=ROUND_HALF_UP))


def custo_do_atraso(valor: float, fator_dia: float, atraso: timedelta) -> float:
    dias = Decimal(str(atraso.total_seconds())) / Decimal(86400)
    custo = Decimal(str(valor)) * Decimal(str(fator_dia)) * dias
    return float(custo.quantize(CENTAVO, rounding=ROUND_HALF_UP))


async def fator_atraso(
    os: OsEmRisco, conhecimento: ConhecimentoCliente, settings: Settings
) -> tuple[float | None, str]:
    """(fator por dia, fonte). Primeiro o conhecimento (E7); senão, a estimativa do `Settings`."""
    if os.tipo_insumo is None:
        return None, "OS sem tipo de insumo"
    try:
        fator = await conhecimento.fator_perda(os.tipo_insumo, CONDICAO_ATRASO)
    except ConhecimentoIndisponivel as e:
        logger.warning("fator de atraso indisponível no conhecimento: %s", e)
        fator = None
    if fator and fator.get("fator") is not None:
        return float(fator["fator"]), str(fator.get("fonte_ou_estimativa") or "sem fonte")
    return settings.fator_atraso_dia, settings.fonte_fator_atraso


def vencimento(proposta: datetime, validos: list[datetime]) -> tuple[datetime, datetime | None]:
    """(quando a janela proposta vence, início da próxima janela boa ou `None`)."""
    for i, bloco in enumerate(blocos(validos)):
        if bloco[0] <= proposta <= bloco[-1]:
            seguintes = blocos(validos)[i + 1 :]
            return bloco[-1] - ANTECEDENCIA, (seguintes[0][0] if seguintes else None)
    depois = [t for t in validos if t > proposta]
    return proposta - ANTECEDENCIA, (depois[0] if depois else None)


async def calcular_custos_atraso(
    plano: PlanoProposto,
    contexto: ContextoPlano,
    conhecimento: ConhecimentoCliente,
    settings: Settings,
    agora: datetime,
) -> list[CustoAtraso]:
    custos: list[CustoAtraso] = []
    for opcao in plano.opcoes:
        os = contexto.os_por_id[opcao.os_id]
        violacoes = contexto.violacoes_por_os.get(opcao.os_id, [])
        validos = janelas_climaticas(contexto.horas, violacoes, os.duracao_horas, agora)
        vence_em, proxima = vencimento(opcao.novo_inicio, validos)
        valor = valor_aplicacao(os)
        fator, fonte = await fator_atraso(os, conhecimento, settings)
        custo = None
        if valor is not None and fator is not None and proxima is not None:
            custo = custo_do_atraso(valor, fator, proxima - opcao.novo_inicio)
        if valor is None:
            fonte = "OS sem dose ou preço"
        custos.append(
            CustoAtraso(
                os_id=opcao.os_id,
                talhao=os.talhao,
                valor_aplicacao_rs=valor,
                fator_dia=fator,
                fonte_fator=fonte,
                janela_proposta=opcao.novo_inicio,
                vence_em=vence_em,
                proxima_janela=proxima,
                custo_perder_janela_rs=custo,
            )
        )
    return custos


def formatar_duracao(intervalo: timedelta) -> str:
    """ "45 min", "5 h", "1 dia e 3 h" (arredonda para baixo: o gerente não pode contar com a sobra)."""
    minutos = max(0, int(intervalo.total_seconds() // 60))
    if minutos < 60:
        return f"{minutos} min"
    horas = minutos // 60
    if horas < 24:
        return f"{horas} h"
    dias, resto = divmod(horas, 24)
    texto = f"{dias} dia" + ("s" if dias > 1 else "")
    return f"{texto} e {resto} h" if resto else texto


def _estimativa(fonte: str) -> str:
    return " (estimativa)" if fonte.lower().startswith("estimativa") else ""


def linha_custo(c: CustoAtraso, agora: datetime) -> str:
    """Uma linha por OS: quando a janela vence e quanto custa perdê-la."""
    janela = formatar_data_longa(c.janela_proposta)
    if agora >= c.vence_em:
        inicio = f"OS {c.os_id} ({c.talhao}): a janela proposta de {janela} já venceu."
        return f"{inicio} Responda Ver opções para receber outra data."
    inicio = (
        f"OS {c.os_id} ({c.talhao}): a janela de {janela} vence em {formatar_duracao(c.vence_em - agora)}."
    )
    if c.proxima_janela is None:
        if c.valor_aplicacao_rs is None or c.fator_dia is None:
            return f"{inicio} Se ela passar, não há outra janela boa na previsão de 5 dias."
        por_dia = custo_do_atraso(c.valor_aplicacao_rs, c.fator_dia, timedelta(days=1))
        return (
            f"{inicio} Se ela passar, não há outra janela boa na previsão de 5 dias;"
            f" cada dia de atraso custa {formatar_reais(por_dia)}{_estimativa(c.fonte_fator)}."
        )
    proxima = formatar_data_longa(c.proxima_janela)
    if c.custo_perder_janela_rs is None:
        sem_custo = f"custo não calculado: {c.fonte_fator}"
        return f"{inicio} Se ela passar, a próxima janela boa é {proxima} ({sem_custo})."
    return (
        f"{inicio} Se ela passar, a próxima janela boa é {proxima}, e esperar até lá custa"
        f" {formatar_reais(c.custo_perder_janela_rs)}{_estimativa(c.fonte_fator)}."
    )


def texto_lembrete(
    incidente_id: int, plano_id: int, criado_em: datetime, agora: datetime, custos: list[CustoAtraso]
) -> str:
    linhas = [
        f"Incidente {incidente_id}: sem decisão no plano {plano_id} há {formatar_duracao(agora - criado_em)}."
        " As OS seguem suspensas; o plano ainda pode ser aprovado."
    ]
    linhas += [linha_custo(c, agora) for c in custos]
    return "\n".join(linhas)
