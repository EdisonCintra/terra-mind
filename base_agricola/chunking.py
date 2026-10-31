import re
import unicodedata

from base_agricola.manifesto import Fonte
from base_agricola.modelos import Secao, Trecho
from base_agricola.pdf import PaginaTexto

CABECALHOS: dict[str, Secao] = {
    "CONDICOES CLIMATICAS": "condicoes_climaticas",
    "CONDICOES METEOROLOGICAS": "condicoes_climaticas",
    "CONDICOES DE APLICACAO": "condicoes_climaticas",
    "MODO DE APLICACAO": "modo_aplicacao",
    "INTERVALO DE SEGURANCA": "intervalo_seguranca",
    "LIMITACOES DE USO": "limitacoes_uso",
}


def _normalizar(linha: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", linha).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.upper().split())


def _secao_do_cabecalho(linha: str) -> Secao | None:
    """Seção que a linha abre, ou None se a linha não é cabeçalho."""
    normal = _normalizar(linha)
    for chave, secao in CABECALHOS.items():
        if normal.startswith(chave):
            return secao
    letras = re.sub(r"[^A-Za-zÀ-ú]", "", linha)
    if len(letras) >= 10 and linha.strip() == linha.strip().upper() and not re.search(r"\d", linha):
        return "outra"
    return None


def dividir_em_trechos(fonte: Fonte, paginas: list[PaginaTexto], max_chars: int = 500) -> list[Trecho]:
    """Divide por seção da bula e por tamanho, sem nunca juntar texto de páginas diferentes.

    500 caracteres: com trechos maiores, a frase do limite de chuva se dilui e sai do top 3 da busca.
    """
    trechos: list[Trecho] = []
    secao: Secao = "outra" if fonte.tipo_doc == "bula" else "geral"
    buffer: list[str] = []

    def fechar(pagina: int) -> None:
        texto = "\n".join(buffer).strip()
        buffer.clear()
        if texto:
            trechos.append(Trecho(doc_id=fonte.id, produto=fonte.produto, tipo_doc=fonte.tipo_doc,
                                  culturas=fonte.culturas, pagina=pagina, secao=secao,
                                  ordem=len(trechos), texto=texto))

    for pagina in paginas:
        for linha in pagina.texto.splitlines():
            if not linha.strip():
                continue
            nova = _secao_do_cabecalho(linha) if fonte.tipo_doc == "bula" else None
            if nova is not None and nova != secao:
                fechar(pagina.pagina)
                secao = nova
            while len(linha) > max_chars:
                fechar(pagina.pagina)
                buffer.append(linha[:max_chars])
                linha = linha[max_chars:]
            if sum(len(b) + 1 for b in buffer) + len(linha) > max_chars:
                fechar(pagina.pagina)
            buffer.append(linha)
        fechar(pagina.pagina)
    return trechos
