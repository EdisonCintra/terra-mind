"""Cliente da API Agrofit (Embrapa): dados do produto e link da bula. A bula em PDF vem por upload.

O servidor do MAPA recusa download por script (docs/pesquisa/agrofit-spike.md), então só o link é usado.
"""

import re
import time
import unicodedata
from collections.abc import Callable
from datetime import datetime

import httpx
from pydantic import BaseModel

URL_TOKEN = "https://api.cnptia.embrapa.br/token"
URL_BUSCA = "https://api.cnptia.embrapa.br/agrofit/v1/search/produtos-formulados"
MARGEM_TOKEN_S = 60  # renova o token quando faltar menos que isto para expirar


class ProdutoAgrofit(BaseModel):
    registro_mapa: str
    produto: str
    titular: str
    classe: str
    culturas: list[str]
    url_bula: str | None


def slug(texto: str) -> str:
    """'Abacus HC' -> 'abacus-hc' (sem acento, minúsculas, hífens)."""
    ascii_ = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")


def _data_inclusao(documento: dict) -> datetime:
    return datetime.strptime(documento["data_inclusao"], "%d/%m/%Y %H:%M:%S")


def parse_produto(item: dict) -> ProdutoAgrofit | None:
    """Item da resposta do Agrofit; None se faltar a marca comercial (não dá para nomear o produto)."""
    marcas = item.get("marca_comercial") or []
    if not marcas:
        return None
    culturas = list(dict.fromkeys(u["cultura"] for u in item.get("indicacao_uso") or [] if u.get("cultura")))
    bulas = [d for d in item.get("documento_cadastrado") or [] if d.get("tipo_documento") == "Bula"]
    classes = item.get("classe_categoria_agronomica") or [""]
    return ProdutoAgrofit(
        registro_mapa=str(item["numero_registro"]),
        produto=marcas[0],
        titular=item.get("titular_registro") or "",
        classe=classes[0].lower(),
        culturas=culturas,
        url_bula=max(bulas, key=_data_inclusao)["url"] if bulas else None,
    )


class ClienteAgrofit:
    def __init__(self, http: httpx.Client, client_id: str, client_secret: str,
                 relogio: Callable[[], float] = time.monotonic):
        self.http = http
        self.credenciais = (client_id, client_secret)
        self.relogio = relogio
        self._token: str | None = None
        self._expira_em = 0.0

    def _cabecalho(self) -> dict[str, str]:
        if self._token is None or self.relogio() >= self._expira_em - MARGEM_TOKEN_S:
            resposta = self.http.post(URL_TOKEN, data={"grant_type": "client_credentials"},
                                      auth=self.credenciais)
            resposta.raise_for_status()
            dados = resposta.json()
            self._token = dados["access_token"]
            self._expira_em = self.relogio() + float(dados["expires_in"])
        return {"Authorization": f"Bearer {self._token}"}

    def _consultar(self, **filtros: str) -> list[ProdutoAgrofit]:
        resposta = self.http.get(URL_BUSCA, params=filtros, headers=self._cabecalho())
        resposta.raise_for_status()
        return [p for p in map(parse_produto, resposta.json()) if p]

    def buscar(self, q: str) -> list[ProdutoAgrofit]:
        return self._consultar(marca_comercial=q)

    def obter(self, registro_mapa: str) -> ProdutoAgrofit | None:
        return next((p for p in self._consultar(numero_registro=registro_mapa)
                     if p.registro_mapa == registro_mapa), None)
