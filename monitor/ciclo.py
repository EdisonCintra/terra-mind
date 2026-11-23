import logging
from datetime import datetime, timedelta
from typing import Literal

from monitor.avaliacao import avaliar
from monitor.fontes import FontePrevisao, chave_grade
from monitor.modelos import Gatilho, Limiares, Previsao, ResultadoCiclo
from monitor.notificador import Notificador

log = logging.getLogger("monitor")

# Condições que só importam com pulverização marcada: sem OS de aplicação na janela do gatilho, elas
# ficam só no mapa de risco (/risco). Chuva, vento e umidade abrem incidente como sempre.
SO_COM_OS = {"rajada", "delta_t", "inversao"}
# Igual a `agents.nodes.contexto.ALCANCE_ANTES_DA_CHUVA` (um teste confere): o Contexto também pega a OS que
# começa até 24 h antes do gatilho.
ALCANCE_ANTES_DO_GATILHO = timedelta(hours=24)


def _com_os(gatilhos: list[Gatilho], repo, talhao_id: int, agora: datetime) -> list[Gatilho]:
    """Tira os gatilhos de `SO_COM_OS` sem OS que o Contexto do `agents` encontraria para eles: início previsto
    em [max(agora, inicio − 24 h), fim]. Com um critério mais largo que o dele, o incidente aberto aqui seria
    encerrado lá como "nenhuma OS" e reaberto aqui a cada ciclo."""
    return [g for g in gatilhos if g.tipo not in SO_COM_OS
            or repo.tem_os_na_janela(talhao_id, max(agora, g.inicio - ALCANCE_ANTES_DO_GATILHO), g.fim)]


def executar_ciclo(fonte: FontePrevisao, repo, notificador: Notificador, limiares: Limiares,
                   agora: datetime, talhao_ids: list[int] | None = None,
                   origem: Literal["live", "replay"] = "live") -> ResultadoCiclo:
    """Um passe por todos os talhões. Erro num talhão vira mensagem e o ciclo segue.

    `origem="replay"` marca o incidente para não entrar no histórico.
    Busca e grava a previsão de todo talhão (inclusive com incidente aberto); só então avalia.
    Incidente ainda `aberto` é avisado de novo: o aviso pode ter falhado e `/iniciar` é idempotente.
    """
    incidentes, erros = [], []
    cache: dict = {}  # uma consulta por ponto de grade, só dentro deste ciclo; falha também fica guardada

    def buscar(talhao) -> Previsao:
        chave = chave_grade(talhao.lat, talhao.lon)
        if chave not in cache:
            try:
                cache[chave] = fonte.obter(talhao.lat, talhao.lon, agora)
            except Exception as erro:  # noqa: BLE001 - repetir a consulta falharia do mesmo jeito
                cache[chave] = erro
        resultado = cache[chave]
        if isinstance(resultado, Exception):
            raise resultado
        return resultado

    for talhao in repo.talhoes(talhao_ids):
        try:
            reaviso = repo.tem_incidente_aberto(talhao.id)
            incidente_id = repo.incidente_sem_andamento(talhao.id) if reaviso else None
            previsao = None
            try:
                previsao = buscar(talhao)
                repo.gravar_previsao(talhao.id, fonte.nome, previsao)
            except Exception as erro:  # noqa: BLE001 - sem previsão ainda há reaviso; com previsão, ainda há avaliação
                log.exception("previsão do talhão %s", talhao.id)
                erros.append(f"talhão {talhao.id}: {erro}")
            if reaviso:
                if incidente_id is None:
                    continue
            else:
                if previsao is None:
                    continue
                gatilhos = _com_os(avaliar(previsao.horas, limiares, agora), repo, talhao.id, agora)
                if not gatilhos:
                    continue
                # o `agents` não aceita None nos campos básicos: hora incompleta fica fora do snapshot
                snapshot = [h for h in previsao.horas if h.completa]
                incidente_id = repo.criar_incidente(talhao.id, gatilhos, snapshot, origem)
                incidentes.append(incidente_id)
        except Exception as erro:  # noqa: BLE001 - um talhão ruim não para os outros
            log.exception("talhão %s", talhao.id)
            erros.append(f"talhão {talhao.id}: {erro}")
            continue
        try:
            notificador.iniciar(incidente_id)
            if reaviso:
                incidentes.append(incidente_id)
        except Exception as erro:  # noqa: BLE001 - o incidente fica gravado e o próximo ciclo avisa de novo
            log.exception("notificar incidente %s", incidente_id)
            erros.append(f"notificar incidente {incidente_id}: {erro}")
    return ResultadoCiclo(incidentes=incidentes, erros=erros, executado_em=agora)
