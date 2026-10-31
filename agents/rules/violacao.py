"""Conferência em código da violação citada pelo Avaliador (LLM).

O LLM às vezes marca `viola=true` num item cuja própria justificativa diz "não há violação" (a
previsão não passa do limite). Como a suspensão da OS depende dessa marca, os números são conferidos
aqui: uma violação só vale se o valor previsto ultrapassa o limite citado.

Sentido dos limites: chuva, vento e temperatura violam ao passar do máximo da bula; umidade viola ao
ficar abaixo do mínimo. Limites mínimos de vento ou temperatura (raros) não são reconhecidos e a
violação é descartada, o que é o lado seguro: sem evidência a OS não é suspensa (decisão A5).
"""

from agents.modelos import ViolacaoOs

PARAMETROS_COM_MINIMO = {"umidade"}


def violacao_sustentada(v: ViolacaoOs) -> bool:
    """True se a violação não é contradita pelos próprios números (ou se não há número para conferir)."""
    if not v.viola:
        return True
    if v.parametro is None or v.limite is None or v.valor_previsto is None:
        return True
    if v.parametro in PARAMETROS_COM_MINIMO:
        return v.valor_previsto < v.limite
    return v.valor_previsto > v.limite
