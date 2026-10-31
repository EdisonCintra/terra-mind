import sys
from pathlib import Path

# O `adk web`/`adk run` importa esta pasta solta; o pacote `agents` está na raiz do monorepo (D3).
_RAIZ = str(Path(__file__).resolve().parents[3])
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from . import agent  # noqa: E402, F401
