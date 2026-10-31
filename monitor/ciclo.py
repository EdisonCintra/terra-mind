import logging
from datetime import datetime

from monitor.avaliacao import avaliar
from monitor.fontes import FontePrevisao
from monitor.modelos import Limiares, ResultadoCiclo
from monitor.notificador import Notificador

log = logging.getLogger("monitor")


def executar_ciclo(fonte: FontePrevisao, repo, notificador: Notificador, limiares: Limiares,
                   agora: datetime, talhao_ids: list[int] | None = None) -> ResultadoCiclo:
    """Um passe por todos os talhões. Erro num talhão vira mensagem e o ciclo segue."""
    incidentes, erros = [], []
    for talhao in repo.talhoes(talhao_ids):
        try:
            if repo.tem_incidente_aberto(talhao.id):
                continue
            horas = fonte.obter(talhao.lat, talhao.lon, agora)
            gatilhos = avaliar(horas, limiares, agora)
            if not gatilhos:
                continue
            incidente_id = repo.criar_incidente(talhao.id, gatilhos, horas)
            incidentes.append(incidente_id)
        except Exception as erro:  # noqa: BLE001 - um talhão ruim não para os outros
            log.exception("talhão %s", talhao.id)
            erros.append(f"talhão {talhao.id}: {erro}")
            continue
        try:
            notificador.iniciar(incidente_id)
        except Exception as erro:  # noqa: BLE001 - o incidente fica gravado; agents pode retomar
            log.exception("notificar incidente %s", incidente_id)
            erros.append(f"notificar incidente {incidente_id}: {erro}")
    return ResultadoCiclo(incidentes=incidentes, erros=erros, executado_em=agora)
