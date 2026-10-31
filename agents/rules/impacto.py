"""Perda em R$ = area_ha × dose_por_ha × preco_unitario × fator. Sempre em código."""

import logging
from decimal import ROUND_HALF_UP, Decimal

from agents.clientes.conhecimento import ConhecimentoCliente, ConhecimentoIndisponivel
from agents.modelos import Impacto, OsEmRisco

logger = logging.getLogger(__name__)

CENTAVO = Decimal("0.01")


def calcular_perda(area_ha: float, dose_por_ha: float, preco_unitario: float, fator: float) -> float:
    perda = (
        Decimal(str(area_ha)) * Decimal(str(dose_por_ha)) * Decimal(str(preco_unitario)) * Decimal(str(fator))
    )
    return float(perda.quantize(CENTAVO, rounding=ROUND_HALF_UP))


async def calcular_impacto(os: OsEmRisco, condicao: str, conhecimento: ConhecimentoCliente) -> Impacto:
    """Busca o fator por `(tipo_insumo, condicao)` (contrato 11). Sem fator, a perda fica `None`."""
    base = {
        "os_id": os.os_id,
        "area_ha": os.area_ha,
        "dose_por_ha": os.dose_por_ha,
        "preco_unitario": os.preco_unitario,
    }
    if os.dose_por_ha is None or os.preco_unitario is None or os.tipo_insumo is None:
        return Impacto(**base, fator=None, fonte_fator="OS sem dose, preço ou tipo de insumo", perda_rs=None)
    try:
        fator = await conhecimento.fator_perda(os.tipo_insumo, condicao)
    except ConhecimentoIndisponivel as e:
        logger.warning("fator de perda indisponível: %s", e)
        return Impacto(**base, fator=None, fonte_fator="serviço de conhecimento indisponível", perda_rs=None)
    if not fator or fator.get("fator") is None:
        return Impacto(**base, fator=None, fonte_fator="sem fator cadastrado", perda_rs=None)
    valor = float(fator["fator"])
    return Impacto(
        **base,
        fator=valor,
        fonte_fator=str(fator.get("fonte_ou_estimativa") or "sem fonte"),
        perda_rs=calcular_perda(os.area_ha, os.dose_por_ha, os.preco_unitario, valor),
    )


def formatar_reais(valor: float | None) -> str:
    if valor is None:
        return "não calculada"
    inteiro, centavos = f"{valor:,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"
