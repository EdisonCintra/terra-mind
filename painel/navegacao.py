"""Menu lateral do painel: uma lista só, usada pelo `layout.html` em todas as telas."""

from typing import NamedTuple

TODOS = frozenset({"gerente", "operador"})  # contrato 23: papel é gerente ou operador


class ItemMenu(NamedTuple):
    rota: str
    rotulo: str
    papeis: frozenset[str]


MENU: list[ItemMenu] = [
    ItemMenu("/visao-geral", "Visão geral", TODOS),
    ItemMenu("/cadastros", "Cadastros", frozenset({"gerente"})),
    ItemMenu("/cadastros/os", "Ordens de serviço", frozenset({"operador"})),  # o operador cria e edita OS (F3)
    ItemMenu("/usuarios", "Usuários", frozenset({"gerente"})),
    ItemMenu("/fazenda", "Fazenda", TODOS),
    ItemMenu("/mapa", "Mapa", TODOS),
    ItemMenu("/agenda", "Agenda", TODOS),
    ItemMenu("/perda", "Perda evitada", TODOS),
    ItemMenu("/historico", "Histórico", TODOS),
    ItemMenu("/bula", "Pergunte à bula", TODOS),
    ItemMenu("/relatorios", "Relatórios", TODOS),
]

VAGAS_BARRA = 5  # barra inferior do celular


def barra_mobile(itens: list[dict], vagas: int = VAGAS_BARRA) -> tuple[list[dict], list[dict]]:
    """Barra do celular: só itens com tela (`existe`). Se passarem de `vagas - 1`, a última vaga vira "Mais".

    Devolve os itens com `na_barra` marcado e a lista do "Mais" (todos os itens com tela; vazia sem "Mais").
    """
    ativos = [i for i in itens if i["existe"]]
    com_mais = len(ativos) > vagas - 1
    na_barra = {i["rota"] for i in (ativos[: vagas - 1] if com_mais else ativos)}
    marcados = [{**i, "na_barra": i["rota"] in na_barra} for i in itens]
    return marcados, (ativos if com_mais else [])
