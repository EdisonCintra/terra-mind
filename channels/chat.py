"""Chat do TerraMind no terminal, como uma conversa de WhatsApp com o gerente (canal `terminal`).

    uv run python -m channels.chat                       # usa AGENTS_URL e AGENTS_TOKEN do .env
    uv run python -m channels.chat --url http://localhost:8000

O `agents` precisa estar com `CHANNEL=terminal`. A janela fica aberta: alertas e planos de reagendamento
aparecem a qualquer momento acima da linha de digitação, e você escreve quando quiser:

    sim | nao | opcoes   responde o plano pendente mais recente     sim 12   responde o plano 12
    pendentes            lista os planos sem resposta                ajuda    mostra os comandos
    outro texto          pergunta ao assistente (OS, previsão, bula) /sair    fecha o chat (Ctrl+D também)

Se o `agents` cair ou reiniciar, o chat avisa e reconecta sozinho.
"""

import argparse
import asyncio
import html
import os
import uuid
from datetime import datetime

import httpx
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout

from channels.teste_bot import carregar_env

ESPERA_S = 25  # long polling: o agents segura o GET até chegar mensagem ou passar este tempo
SAIR = {"/sair", "/exit", "/quit"}
CORES = {
    "aprovacao": "ansiyellow",
    "alerta": "ansired",
    "erro": "ansired",
    "decisao": "ansigreen",
    "bula": "ansicyan",
    "operador": "ansimagenta",
    "info": "ansibrightblack",
    "mensagem": "ansiblue",
}
RECUO = " " * 8


def hora(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts).strftime("%H:%M")
    except (TypeError, ValueError):
        return "--:--"


def formatar(m: dict) -> str:
    """Uma mensagem da conversa em HTML do prompt_toolkit: cabeçalho colorido e o texto recuado."""
    cor = CORES.get(m.get("tipo", ""), "ansiwhite")
    quem = "Você" if m.get("de") == "voce" else "TerraMind"
    titulo = f" · {m['titulo']}" if m.get("titulo") else ""
    quando = f"<ansibrightblack>[{hora(m.get('ts', ''))}]</ansibrightblack>"
    cabecalho = f"{quando} <b><{cor}>{quem}{html.escape(titulo)}</{cor}></b>"
    linhas = (m.get("texto") or "").splitlines() or [""]
    corpo = "\n".join(RECUO + html.escape(linha) for linha in linhas)
    return f"{cabecalho}\n{corpo}\n"


def imprimir(markup: str) -> None:
    print_formatted_text(HTML(markup))


def aviso(texto: str) -> None:
    imprimir(f"<ansibrightblack>— {html.escape(texto)}</ansibrightblack>")


class Chat:
    def __init__(self, url: str, token: str | None):
        self.url = url.rstrip("/")
        self.origem = uuid.uuid4().hex[:12]
        cabecalhos = {"X-Agents-Token": token} if token else {}
        self.http = httpx.AsyncClient(base_url=self.url, headers=cabecalhos, timeout=ESPERA_S + 15)
        self.ultimo = 0
        self.instancia: str | None = None
        self.status = "conectando..."

    async def ouvir(self) -> None:
        """Busca as mensagens novas para sempre; reconecta se o agents cair ou reiniciar."""
        falhou = False
        primeira = True
        while True:
            try:
                r = await self.http.get(
                    "/terminal/mensagens",
                    params={"depois": self.ultimo, "espera": 0 if primeira else ESPERA_S},
                )
                if r.status_code == 404:
                    raise RuntimeError("o agents não está com CHANNEL=terminal")
                if r.status_code == 401:
                    raise RuntimeError("AGENTS_TOKEN diferente do configurado no agents")
                r.raise_for_status()
                dados = r.json()
            except (httpx.HTTPError, RuntimeError, ValueError) as e:
                if not falhou:
                    motivo = str(e) if isinstance(e, RuntimeError) else f"sem conexão com {self.url}"
                    aviso(f"{motivo}; tentando de novo...")
                falhou = True
                self.status = "desconectado"
                await asyncio.sleep(3)
                continue
            if falhou:
                aviso("conectado de novo")
                falhou = False
            self.status = f"conectado a {self.url}"
            instancia = dados.get("instancia")
            if self.instancia is not None and instancia != self.instancia:
                aviso("o agents reiniciou; a conversa anterior ficou só aqui na tela")
                self.ultimo = 0
                self.instancia = instancia
                continue
            self.instancia = instancia
            for m in dados.get("mensagens", []):
                self.ultimo = max(self.ultimo, m["id"])
                if m.get("origem") == self.origem:
                    continue  # o que eu digitei já está na tela
                imprimir(formatar(m))
            primeira = False

    async def enviar(self, texto: str) -> None:
        try:
            r = await self.http.post("/terminal/mensagens", json={"texto": texto, "origem": self.origem})
            r.raise_for_status()
        except httpx.HTTPError as e:
            aviso(f"mensagem não enviada ({type(e).__name__}); o agents está no ar?")

    def barra(self) -> HTML:
        return HTML(f" TerraMind · {html.escape(self.status)} · /sair para fechar ")

    async def rodar(self) -> None:
        sessao: PromptSession = PromptSession()
        with patch_stdout(raw=True):
            imprimir("<b><ansigreen>TerraMind</ansigreen></b> · chat do gerente (canal terminal)")
            escuta = asyncio.create_task(self.ouvir())
            try:
                while True:
                    texto = (
                        await sessao.prompt_async(
                            HTML("<b>você › </b>"), bottom_toolbar=self.barra, refresh_interval=1
                        )
                    ).strip()
                    if texto.lower() in SAIR:
                        break
                    if texto:
                        await self.enviar(texto)
            except (EOFError, KeyboardInterrupt):
                pass
            finally:
                escuta.cancel()
                await asyncio.gather(escuta, return_exceptions=True)
                await self.http.aclose()


def main() -> None:
    carregar_env()
    parser = argparse.ArgumentParser(description="Chat do TerraMind no terminal (CHANNEL=terminal).")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--token", default=os.environ.get("AGENTS_TOKEN") or None)
    args = parser.parse_args()
    asyncio.run(Chat(args.url, args.token).rodar())


if __name__ == "__main__":
    main()
