import hashlib
import math
import os
import re
from typing import Protocol

MODELO_PADRAO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class Embedder(Protocol):
    dimensao: int

    def embed(self, textos: list[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    """Embeddings reais (multilíngue). Baixa o modelo na primeira vez."""

    def __init__(self, model_name: str | None = None):
        from fastembed import TextEmbedding

        self.modelo = TextEmbedding(model_name or os.environ.get("EMBEDDING_MODEL", MODELO_PADRAO),
                                    cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
        self.dimensao = len(next(iter(self.modelo.embed(["teste"]))))

    def embed(self, textos: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.modelo.embed(textos)]


class EmbedderDeterministico:
    """Saco de palavras com hash: sem rede nem modelo, para testes e desenvolvimento."""

    def __init__(self, dimensao: int = 32):
        self.dimensao = dimensao

    def embed(self, textos: list[str]) -> list[list[float]]:
        return [self._vetor(t) for t in textos]

    def _vetor(self, texto: str) -> list[float]:
        v = [0.0] * self.dimensao
        for palavra in re.findall(r"\w+", texto.lower()):
            v[int(hashlib.md5(palavra.encode()).hexdigest(), 16) % self.dimensao] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]
