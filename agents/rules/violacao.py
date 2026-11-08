"""Conferência em código da violação citada pelo Avaliador (LLM).

O LLM às vezes marca `viola=true` num item cuja própria justificativa diz "não há violação" (a
previsão não passa do limite). Como a suspensão da OS depende dessa marca, os números são conferidos
aqui: uma violação só vale se o valor previsto ultrapassa o limite citado.

Sentido dos limites (`ViolacaoOs.sentido_efetivo`): limite máximo viola acima dele (chuva, vento,
temperatura, rajada, Delta T, inversão), mínimo viola abaixo (umidade; e vento ou Delta T quando a bula
dá uma faixa, como "vento entre 2 e 10 km/h"). Inversão térmica: limite 0 e valor previsto 1.
"""

from agents.modelos import ViolacaoOs


def violacao_sustentada(v: ViolacaoOs) -> bool:
    """True se a violação não é contradita pelos próprios números (ou se não há número para conferir)."""
    if not v.viola:
        return True
    if v.parametro is None or v.limite is None or v.valor_previsto is None:
        return True
    if v.sentido_efetivo == "minimo":
        return v.valor_previsto < v.limite
    return v.valor_previsto > v.limite
