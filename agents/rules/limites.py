"""Limites de aplicação de um produto, para a agenda (G2) e a avaliação de janela (G3).

Vale primeiro o que foi lido da bula (`conhecimento.limites_produto`, contrato 26), com trecho e
página. Para o parâmetro que a bula não traz, ou enquanto ela não foi lida, vale a referência genérica
abaixo, sempre marcada como genérica: ela nunca aparece com página de bula.
"""

from agents.modelos import HoraPrevisao, LimiteProduto
from agents.rules.janelas import SERIES

FONTE_GENERICA = "referência genérica de tecnologia de aplicação, não é a bula do produto"

# Faixas usuais de boas práticas de pulverização (vento de 3 a 10 km/h, até 30 °C, umidade a partir de
# 55%, Delta T de 2 a 10 °C e sem chuva na aplicação e nas 2 h seguintes). São o ponto de partida até a
# tabela do E7 chegar; o limite da bula, quando existe, sempre substitui o genérico.
GENERICOS: tuple[LimiteProduto, ...] = (
    LimiteProduto(parametro="chuva", sentido="maximo", limite=1, unidade="mm", janela_h=2, generico=True),
    LimiteProduto(parametro="vento", sentido="maximo", limite=10, unidade="km/h", generico=True),
    LimiteProduto(parametro="vento", sentido="minimo", limite=3, unidade="km/h", generico=True),
    LimiteProduto(parametro="temperatura", sentido="maximo", limite=30, unidade="°C", generico=True),
    LimiteProduto(parametro="umidade", sentido="minimo", limite=55, unidade="%", generico=True),
    LimiteProduto(parametro="delta_t", sentido="maximo", limite=10, unidade="°C", generico=True),
    LimiteProduto(parametro="delta_t", sentido="minimo", limite=2, unidade="°C", generico=True),
)


def limites_efetivos(da_bula: list[LimiteProduto]) -> list[LimiteProduto]:
    """Os limites da bula e, para cada (parâmetro, sentido) que ela não traz, o genérico."""
    cobertos = {limite.chave for limite in da_bula}
    return [*da_bula, *(g for g in GENERICOS if g.chave not in cobertos)]


def com_dado(limites: list[LimiteProduto], horas: list[HoraPrevisao]) -> list[LimiteProduto]:
    """Tira o limite genérico de um parâmetro que a previsão não traz (Delta T e rajada são opcionais,
    contrato 21). O limite da bula fica mesmo sem dado: aí a janela é recusada, porque não há como
    mostrar que ela respeita a bula."""

    def tem_dado(limite: LimiteProduto) -> bool:
        if limite.parametro == "chuva":
            return True
        valor_da_hora = SERIES[limite.parametro][0]
        return any(valor_da_hora(h) is not None for h in horas)

    return [limite for limite in limites if not limite.generico or tem_dado(limite)]


def fonte_do_limite(limite: LimiteProduto) -> str:
    """ "bula, p. 7" ou a marca de referência genérica: o que acompanha todo motivo mostrado ao gerente."""
    if limite.generico or limite.pagina is None:
        return "referência genérica"
    return f"bula, p. {limite.pagina}"
