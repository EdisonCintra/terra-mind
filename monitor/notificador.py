from typing import Protocol

import httpx


class Notificador(Protocol):
    def iniciar(self, incidente_id: int) -> None: ...


class NotificadorHttp:
    """Avisa o runner do ADK que há um incidente novo (contrato 3)."""

    def __init__(self, agents_url: str):
        self.agents_url = agents_url.rstrip("/")

    def iniciar(self, incidente_id: int) -> None:
        httpx.post(f"{self.agents_url}/incidentes/{incidente_id}/iniciar", timeout=5).raise_for_status()
