"""G4: conferência em código das citações do Pergunte à bula.

Mesma garantia do Avaliador: uma citação só vale se o texto citado for literal de um trecho que a
busca devolveu nesta conversa, com a mesma fonte e a mesma página. O LLM pode cortar o trecho com
reticências; cada pedaço precisa estar no texto recuperado. Citação que não confere é descartada e,
sem nenhuma válida, a resposta vira "A bula não traz isso."
"""

import re
import unicodedata

from agents.modelos import CitacaoBula, RespostaBula, ResultadoBula

NAO_ENCONTRADO = "A bula não traz isso."
MINIMO_CARACTERES = 15  # pedaço citado curto demais ("10 km/h") não identifica o trecho
RETICENCIAS = re.compile(r"\.{3,}|…")


def normalizar(texto: str) -> str:
    """Minúsculas, sem acento, aspas e travessões unificados e espaços colapsados (o PDF quebra linhas).

    O ordinal "º" das bulas ("30ºC") vira "°" antes do NFKD, que o transformaria na letra "o".
    """
    texto = texto.replace("º", "°")
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c)).lower()
    texto = re.sub(r"[“”«»\"']", "", texto)
    texto = re.sub(r"[–—]", "-", texto)
    return " ".join(texto.split())


def citacao_confere(citacao: CitacaoBula, vistos: list[dict]) -> dict | None:
    """O trecho recuperado que sustenta a citação, ou `None`."""
    pedacos = [normalizar(p) for p in RETICENCIAS.split(citacao.trecho)]
    pedacos = [p for p in pedacos if p]
    if not pedacos or sum(len(p) for p in pedacos) < MINIMO_CARACTERES:
        return None
    for visto in vistos:
        if visto.get("fonte_doc") != citacao.fonte_doc or visto.get("pagina") != citacao.pagina:
            continue
        texto = normalizar(visto.get("texto") or "")
        if all(p in texto for p in pedacos):
            return visto
    return None


def conferir(resposta: RespostaBula | None, vistos: list[dict]) -> ResultadoBula:
    if resposta is None or not resposta.encontrou:
        return ResultadoBula(encontrou=False, resposta=NAO_ENCONTRADO)
    validas: list[CitacaoBula] = []
    for c in resposta.citacoes:
        visto = citacao_confere(c, vistos)
        if visto is not None:
            validas.append(c.model_copy(update={"produto": visto.get("produto") or c.produto}))
    descartadas = len(resposta.citacoes) - len(validas)
    if not validas:
        return ResultadoBula(encontrou=False, resposta=NAO_ENCONTRADO, descartadas=descartadas)
    return ResultadoBula(
        encontrou=True, resposta=resposta.resposta.strip(), citacoes=validas, descartadas=descartadas
    )
