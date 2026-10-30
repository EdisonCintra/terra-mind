import asyncio
from datetime import timedelta

from fastapi.testclient import TestClient

from agents.api import Servicos, criar_api
from agents.timeout import verificar_timeouts
from tests.fakes import AGORA, FakeCanal, FakeRepo, incidente_chuva


class FakeOrquestrador:
    def __init__(self):
        self.repo = FakeRepo()
        self.repo.incidentes[7] = incidente_chuva()
        self.deps = type("D", (), {"repo": self.repo})()
        self.iniciados: list[int] = []

    def disparar_inicio(self, incidente_id):
        self.iniciados.append(incidente_id)

    async def responder(self, r, esperar=False):
        return await self.repo.registrar_resposta(r.plano_id, r.incidente_id, r.resposta, r.respondido_por)

    async def aguardar(self):
        return None


def cliente():
    orq = FakeOrquestrador()

    async def fabrica():
        return Servicos(orquestrador=orq)

    return TestClient(criar_api(fabrica)), orq


def test_health():
    c, _ = cliente()
    with c:
        assert c.get("/health").json() == {"status": "ok"}


def test_iniciar_202_e_404():
    c, orq = cliente()
    with c:
        assert c.post("/incidentes/7/iniciar").status_code == 202
        assert c.post("/incidentes/99/iniciar").status_code == 404
    assert orq.iniciados == [7]


def test_resposta_ok_repetida_desconhecida_e_invalida():
    c, orq = cliente()
    asyncio.run(orq.repo.criar_plano(7, {"opcoes": []}))
    with c:
        corpo = {"plano_id": 1, "resposta": "SIM", "respondido_por": "painel"}
        assert c.post("/incidentes/7/resposta", json=corpo).status_code == 200
        assert c.post("/incidentes/7/resposta", json=corpo).status_code == 409
        assert c.post("/incidentes/7/resposta", json={**corpo, "plano_id": 5}).status_code == 404
        assert c.post("/incidentes/7/resposta", json={**corpo, "resposta": "TALVEZ"}).status_code == 422


async def test_timeout_escalona_uma_vez():
    repo, canal = FakeRepo(), FakeCanal()
    await repo.criar_plano(7, {"opcoes": []})
    depois = AGORA + timedelta(minutes=31)
    assert await verificar_timeouts(repo, canal, depois, 30) == [1]
    assert await verificar_timeouts(repo, canal, depois + timedelta(minutes=5), 30) == []
    assert len(canal.alertas) == 1 and "seguem suspensas" in canal.alertas[0]


async def test_timeout_ignora_plano_respondido_ou_recente():
    repo, canal = FakeRepo(), FakeCanal()
    await repo.criar_plano(7, {"opcoes": []})
    assert await verificar_timeouts(repo, canal, AGORA + timedelta(minutes=10), 30) == []
    await repo.registrar_resposta(1, 7, "NAO", "gerente")
    assert await verificar_timeouts(repo, canal, AGORA + timedelta(hours=2), 30) == []
