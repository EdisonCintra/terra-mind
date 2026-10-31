from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from base_agricola.dependencias import get_repo
from base_agricola.modelos import Condicao, FatorPerda
from base_agricola.repositorio import RepositorioConhecimento

router = APIRouter()


@router.get("/fatores-perda")
def fator_perda(
    tipo_insumo: str,
    condicao: Condicao,
    repo: Annotated[RepositorioConhecimento, Depends(get_repo)],
) -> FatorPerda:
    fator = repo.fator_perda(tipo_insumo, condicao)
    if fator is None:
        raise HTTPException(status_code=404, detail="fator_nao_cadastrado")
    return fator
