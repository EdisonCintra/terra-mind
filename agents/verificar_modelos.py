"""Confere se cada modelo configurado aceita ferramentas + `output_schema` na OpenAI.

Uso: `uv run python -m agents.verificar_modelos` (precisa de `OPENAI_API_KEY` e `MODEL_*`).
Roda pelo mesmo caminho da produção: o `Agent` como nó de um `Workflow`.
"""

import asyncio
import sys
import time
from typing import get_args

from google.adk import Agent, Workflow
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from agents.config import NomeAgente, Settings
from agents.deps import criar_modelo


class Resultado(BaseModel):
    soma: int
    explicacao: str


async def verificar(nome: NomeAgente, settings: Settings) -> tuple[bool, str]:
    chamadas: list[tuple[int, int]] = []

    def somar(a: int, b: int) -> dict:
        """Soma dois inteiros."""
        chamadas.append((a, b))
        return {"soma": a + b}

    agente = Agent(
        name=f"teste_{nome}",
        model=criar_modelo(settings, nome),
        instruction="Use a ferramenta somar para calcular 17 + 25 e devolva o resultado no formato pedido.",
        tools=[somar],
        output_schema=Resultado,
        include_contents="none",
        mode="single_turn",
    )
    runner = Runner(
        app_name="verificacao",
        agent=Workflow(name="verificacao", edges=[("START", agente)]),
        session_service=InMemorySessionService(),
    )
    sessao = await runner.session_service.create_session(app_name="verificacao", user_id="u")
    saida: dict = {}
    try:
        async for evento in runner.run_async(
            user_id="u",
            session_id=sessao.id,
            new_message=types.Content(role="user", parts=[types.Part(text="ok")]),
        ):
            if evento.output is not None and evento.author == agente.name:
                saida["valor"] = evento.output
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if "valor" not in saida:
        return False, "o workflow terminou sem saída (veja os logs)"
    resultado = Resultado.model_validate(saida["valor"])
    if not chamadas:
        return False, f"não chamou a ferramenta (respondeu {resultado.soma})"
    if resultado.soma != 42:
        return False, f"chamou a ferramenta, mas respondeu {resultado.soma}"
    return True, "ferramenta + output_schema ok"


async def main() -> int:
    settings = Settings()
    if not settings.openai_api_key:
        print("defina OPENAI_API_KEY")
        return 2
    falhas = 0
    for nome in get_args(NomeAgente):
        inicio = time.monotonic()
        ok, detalhe = await verificar(nome, settings)
        falhas += not ok
        duracao = time.monotonic() - inicio
        print(f"{'OK ' if ok else 'ERRO'} {nome:<12} {settings.modelo(nome):<45} {duracao:5.1f}s  {detalhe}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
