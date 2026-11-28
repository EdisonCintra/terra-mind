"""Menu lateral do painel: uma lista só, usada pelo `layout.html` em todas as telas.

Os rótulos seguem o conceito 2 do Figma (Hoje, Mapa, Semana, Acertos, Bula, Relatórios e o grupo Gestão);
as rotas continuam as de antes, para não quebrar links (Telegram, favoritos).
"""

from typing import NamedTuple

TODOS = frozenset({"gerente", "operador"})  # contrato 23: papel é gerente ou operador
GERENTE = frozenset({"gerente"})
OPERADOR = frozenset({"operador"})


class ItemMenu(NamedTuple):
    rota: str
    rotulo: str
    papeis: frozenset[str]
    icone: str = "hoje"  # arquivo em static/icones (sem .svg)
    grupo: str = ""  # "" = menu principal; "gestao" = bloco "Gestão" abaixo dele


MENU: list[ItemMenu] = [
    ItemMenu("/visao-geral", "Hoje", TODOS, "hoje"),
    ItemMenu("/campo", "Executar OS", OPERADOR, "campo"),  # F6: tela do operador no celular
    ItemMenu("/mapa", "Mapa", TODOS, "mapa"),
    ItemMenu("/agenda", "Semana", TODOS, "semana"),
    ItemMenu("/historico", "Acertos", TODOS, "acertos"),
    ItemMenu("/bula", "Bula", TODOS, "bula"),
    ItemMenu("/relatorios", "Relatórios", TODOS, "relatorios"),
    ItemMenu("/fazenda", "Fazenda", TODOS, "fazenda", "gestao"),
    ItemMenu("/perda", "Perda evitada", TODOS, "perda", "gestao"),
    ItemMenu("/cadastros", "Cadastros", GERENTE, "cadastros", "gestao"),
    # o operador cria e edita OS (F3)
    ItemMenu("/cadastros/os", "Ordens de serviço", OPERADOR, "ordens", "gestao"),
    ItemMenu("/usuarios", "Usuários", GERENTE, "usuarios", "gestao"),
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
