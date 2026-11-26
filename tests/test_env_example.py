"""O `.env.example` é copiado para `.env` por quem vai rodar o projeto."""

import re
from pathlib import Path

EXEMPLO = Path(__file__).resolve().parents[1] / ".env.example"


def test_variavel_vazia_nao_leva_comentario_na_mesma_linha():
    """O Compose lê `NOME=   # texto` como o valor "# texto" (vira token, telefone ou URL de verdade)."""
    ruins = [
        linha
        for linha in EXEMPLO.read_text(encoding="utf-8").splitlines()
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\s+#", linha)
    ]
    assert ruins == []
