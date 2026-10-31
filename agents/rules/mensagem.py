"""Mensagem ao gerente, pensada para a tela do celular: curta, em blocos e sem jargão.

O texto é montado só por código a partir do estado (OS, violação citada, perda, janela e máquina).
Nenhum número ou data vem do texto livre de um LLM.
"""

from agents.modelos import Impacto, OsEmRisco, PlanoProposto, ViolacaoOs
from agents.rules.impacto import formatar_reais
from agents.rules.janelas import FUSO
from agents.rules.violacao import violacao_sustentada

DIVISOR = "──────────────"
DIAS = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]
TITULOS = {
    "chuva": "CHUVA PREVISTA",
    "vento": "VENTO FORTE PREVISTO",
    "umidade": "UMIDADE FORA DO LIMITE",
    "temperatura": "TEMPERATURA FORA DO LIMITE",
}
OPERACOES = {"pulverizacao": "Pulverização"}
LIMITE_MOTIVO = 200


def formatar_data_longa(t) -> str:
    """"segunda, 02/11 às 06:00" (hora de Brasília)."""
    local = t.astimezone(FUSO)
    return f"{DIAS[local.weekday()]}, {local:%d/%m} às {local:%H:%M}"


def _operacao(tipo: str) -> str:
    return OPERACOES.get(tipo, tipo.replace("_", " ").capitalize())


def _motivo(v: ViolacaoOs) -> str:
    """Por que a OS foi suspensa, com a página da bula e os números da violação citada."""
    bula = f"A bula (p. {v.pagina})"
    previsto = v.valor_previsto
    if v.parametro == "chuva":
        if v.limite:
            regra = f"limita a chuva a {v.limite:g} mm"
        elif v.janela_h:
            regra = f"diz que chuva em até {v.janela_h:g} h após a aplicação prejudica o produto"
        else:
            regra = "diz que chuva na aplicação prejudica o produto"
        previsao = f" A previsão traz {previsto:g} mm nesse período." if previsto is not None else ""
    elif v.parametro == "vento":
        regra = f"limita o vento a {v.limite:g} km/h na aplicação"
        previsao = f" A previsão chega a {previsto:g} km/h." if previsto is not None else ""
    elif v.parametro == "umidade":
        regra = f"indica {v.limite:g}% de umidade relativa como limite"
        previsao = f" A previsão é de {previsto:g}%." if previsto is not None else ""
    elif v.parametro == "temperatura":
        regra = f"indica {v.limite:g} °C como limite"
        previsao = f" A previsão é de {previsto:g} °C." if previsto is not None else ""
    else:
        return " ".join(v.justificativa.split())[:LIMITE_MOTIVO]
    return f"{bula} {regra}.{previsao}"


def _perda(impacto: Impacto | None) -> str:
    if impacto is None or impacto.perda_rs is None:
        return "Perda evitada: não calculada (sem fator cadastrado)"
    estimativa = " (estimativa)" if impacto.fonte_fator.lower().startswith("estimativa") else ""
    return f"Perda evitada: {formatar_reais(impacto.perda_rs)}{estimativa}"


def compor_mensagem(
    plano: PlanoProposto,
    os_por_id: dict[int, OsEmRisco],
    violacoes_por_os: dict[int, list[ViolacaoOs]],
    impactos: dict[int, Impacto],
    maquinas: dict[int, str] | None = None,
    opcao: tuple[int, int] | None = None,
) -> str:
    """`maquinas`: id → nome (sem o nome, mostra "Máquina N"). `opcao`: (atual, total) das propostas."""
    maquinas = maquinas or {}
    parametros = [
        v.parametro
        for o in plano.opcoes
        for v in violacoes_por_os.get(o.os_id, [])
        if v.viola and v.parametro and violacao_sustentada(v)
    ]
    titulo = TITULOS.get(parametros[0], "RISCO CLIMÁTICO") if parametros else "RISCO CLIMÁTICO"
    linhas = [f"{titulo} - AÇÃO NECESSÁRIA"]
    if opcao:
        linhas.append(f"Opção {opcao[0]} de {opcao[1]}")
    if len(plano.opcoes) == 1:
        unico = os_por_id[plano.opcoes[0].os_id]
        linhas.append(f"A {_operacao(unico.tipo_operacao).lower()} foi pausada por segurança.")
    else:
        linhas.append(f"{len(plano.opcoes)} ordens de serviço foram pausadas por segurança.")

    for o in plano.opcoes:
        os = os_por_id[o.os_id]
        violacoes = [v for v in violacoes_por_os.get(o.os_id, []) if v.viola and violacao_sustentada(v)]
        linhas += ["", DIVISOR, os.talhao, f"{_operacao(os.tipo_operacao)} · {os.produto or 'sem insumo'}"]
        linhas.append(f"OS {os.os_id} · suspensa")
        linhas += ["", "PROPOSTA", f"Nova data: {formatar_data_longa(o.novo_inicio)}"]
        linhas.append(f"Máquina: {maquinas.get(o.maquina_id, f'Máquina {o.maquina_id}')}")
        linhas += ["", _perda(impactos.get(o.os_id))]
        if violacoes:
            linhas += ["", "POR QUÊ"] + [_motivo(v) for v in violacoes]

    linhas += ["", DIVISOR, "Escolha uma opção abaixo."]
    return "\n".join(linhas)
