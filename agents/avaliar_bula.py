"""G4: roda as 20 perguntas de `agents/avaliacao/perguntas_bula.yaml` contra o LLM e a base reais.

    uv run python -m agents.avaliar_bula            # todas
    uv run python -m agents.avaliar_bula 1 5 20     # só algumas (numeração do arquivo, a partir de 1)

Usa o `Settings` (OPENAI_API_KEY, MODEL_BULA, CONHECIMENTO_URL). Não grava no Postgres: a auditoria
vai para um repositório em memória. Sai com código 1 se alguma pergunta falhar.
"""

import asyncio
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from agents.modelos import ResultadoBula
from agents.rules.citacao import NAO_ENCONTRADO, normalizar

ARQUIVO = Path(__file__).parent / "avaliacao" / "perguntas_bula.yaml"


@dataclass
class Caso:
    numero: int
    pergunta: str
    produto: str | None
    encontra: bool
    fonte: str | None
    paginas: list[int]
    deve_conter: list[str]
    gabarito: str


def carregar(arquivo: Path = ARQUIVO) -> list[Caso]:
    itens = yaml.safe_load(arquivo.read_text(encoding="utf-8"))["perguntas"]
    return [
        Caso(
            numero=i,
            pergunta=item["pergunta"],
            produto=item.get("produto"),
            encontra=item.get("encontra", True),
            fonte=item.get("fonte"),
            paginas=item.get("paginas", []),
            deve_conter=item.get("deve_conter", []),
            gabarito=item["gabarito"],
        )
        for i, item in enumerate(itens, start=1)
    ]


def julgar(caso: Caso, r: ResultadoBula) -> list[str]:
    """Lista de problemas; vazia = passou. Toda resposta encontrada precisa mostrar a página."""
    if r.erro:
        return [f"erro: {r.erro}"]
    if not caso.encontra:
        problemas = []
        if r.encontrou or r.citacoes:
            problemas.append("devia dizer que a bula não traz isso")
        if r.resposta != NAO_ENCONTRADO:
            problemas.append(f"resposta diferente de {NAO_ENCONTRADO!r}")
        return problemas
    if not r.encontrou:
        return ["não encontrou" + (f" ({r.descartadas} citação(ões) descartada(s))" if r.descartadas else "")]
    problemas = []
    if not any(c.fonte_doc == caso.fonte and c.pagina in caso.paginas for c in r.citacoes):
        vistas = ", ".join(f"{c.fonte_doc} p. {c.pagina}" for c in r.citacoes)
        problemas.append(f"sem citação de {caso.fonte} p. {caso.paginas} (citou: {vistas})")
    texto = normalizar(r.resposta)
    problemas += [f"resposta sem {p!r}" for p in caso.deve_conter if not re.search(p, texto)]
    return problemas


async def avaliar(numeros: set[int] | None = None) -> int:
    from agents.bula import PergunteBula
    from agents.clientes.conhecimento import ConhecimentoHttp
    from agents.config import Settings
    from agents.deps import Dependencias
    from agents.simulacao import FakeCanal, FakeErp, FakeRepo

    settings = Settings()
    conhecimento = ConhecimentoHttp(settings.conhecimento_url, timeout_s=60)
    deps = Dependencias(
        settings=settings, erp=FakeErp(), conhecimento=conhecimento, repo=FakeRepo(), canal=FakeCanal()
    )
    bula = PergunteBula(deps)
    casos = [c for c in carregar() if numeros is None or c.numero in numeros]
    falhas = 0
    for caso in casos:
        r = await bula.perguntar(caso.pergunta, caso.produto)
        problemas = julgar(caso, r)
        falhas += bool(problemas)
        paginas = ", ".join(f"{c.fonte_doc} p. {c.pagina}" for c in r.citacoes) or "sem citação"
        print(f"[{'ok' if not problemas else 'FALHOU'}] {caso.numero:2d}. {caso.pergunta}")
        print(f"      resposta: {r.resposta}")
        print(f"      citação: {paginas} | esperado: {caso.gabarito}")
        for p in problemas:
            print(f"      -> {p}")
    await conhecimento.fechar()
    print(f"\n{len(casos) - falhas} de {len(casos)} passaram")
    return 1 if falhas else 0


if __name__ == "__main__":
    escolhidas = {int(a) for a in sys.argv[1:]} or None
    sys.exit(asyncio.run(avaliar(escolhidas)))
