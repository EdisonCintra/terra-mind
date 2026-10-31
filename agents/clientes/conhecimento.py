"""Acesso ao serviço de conhecimento do Edison (contratos 10 e 11)."""

import logging
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class ConhecimentoIndisponivel(Exception):
    pass


class ConhecimentoCliente(Protocol):
    async def buscar_trechos(
        self, consulta: str, produto: str | None = None, secao: str | None = None, limite: int = 3
    ) -> list[dict]: ...

    async def fator_perda(self, tipo_insumo: str, condicao: str) -> dict | None: ...


class ConhecimentoHttp:
    def __init__(self, url: str, timeout_s: float = 5):
        self._http = httpx.AsyncClient(base_url=url, timeout=timeout_s)

    async def buscar_trechos(self, consulta, produto=None, secao=None, limite=3):
        corpo = {"consulta": consulta, "limite": limite}
        if produto:
            corpo["produto"] = produto
        if secao:
            corpo["secao"] = secao
        try:
            r = await self._http.post("/trechos/busca", json=corpo)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ConhecimentoIndisponivel(str(e)) from e
        return r.json()

    async def fator_perda(self, tipo_insumo, condicao):
        try:
            r = await self._http.get(
                "/fatores-perda", params={"tipo_insumo": tipo_insumo, "condicao": condicao}
            )
            if r.status_code == 404:
                return None
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ConhecimentoIndisponivel(str(e)) from e
        return r.json()

    async def fechar(self) -> None:
        await self._http.aclose()
