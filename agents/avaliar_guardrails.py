"""Avalia os guardrails do chat (NeMo Guardrails) contra o LLM real, com os casos de
`agents/avaliacao/guardrails.yaml`:

    uv run python -m agents.avaliar_guardrails

Mostra cada caso, os falsos positivos (pergunta legítima barrada) e os falsos negativos (ataque que passou).
Sai com código 1 se algum caso errar. Rode de novo sempre que mudar a política, o modelo
(`MODEL_GUARDRAILS`) ou o `REASONING_EFFORT`.
"""

import asyncio
import sys
import time
from pathlib import Path

import yaml

ARQUIVO = Path(__file__).parent / "avaliacao" / "guardrails.yaml"


async def avaliar() -> int:
    from agents.config import Settings
    from agents.guardrails import Guardrails

    casos = yaml.safe_load(ARQUIVO.read_text(encoding="utf-8"))
    guardrails = Guardrails(Settings())
    erros = {"falso_positivo": 0, "falso_negativo": 0}
    total = 0
    tempos: list[float] = []

    async def julgar(rotulo: str, esperado: bool, verificacao) -> None:
        nonlocal total
        inicio = time.perf_counter()
        veredito = await verificacao
        tempos.append(time.perf_counter() - inicio)
        total += 1
        bloqueou = not veredito.permitida
        if bloqueou == esperado:
            situacao = "ok"
        else:
            situacao = "FALSO POSITIVO" if bloqueou else "FALSO NEGATIVO"
            erros["falso_positivo" if bloqueou else "falso_negativo"] += 1
        acao = "bloqueou" if bloqueou else "permitiu"
        print(f"[{situacao:>14}] {acao:8} {tempos[-1]:4.1f}s  {rotulo[:90]!r}")

    print("— entrada")
    for caso in casos["entrada"]:
        await julgar(caso["texto"], caso["bloqueia"], guardrails.entrada(caso["texto"]))
    print("— saída")
    for caso in casos["saida"]:
        await julgar(caso["resposta"], caso["bloqueia"], guardrails.saida(caso["pergunta"], caso["resposta"]))

    acertos = total - sum(erros.values())
    media = sum(tempos) / len(tempos) if tempos else 0
    print(
        f"\n{acertos} de {total} certos · {erros['falso_positivo']} falso(s) positivo(s) · "
        f"{erros['falso_negativo']} falso(s) negativo(s) · {media:.1f}s por verificação"
    )
    return 1 if sum(erros.values()) else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(avaliar()))
