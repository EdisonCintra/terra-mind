from datetime import datetime, timedelta

from monitor.modelos import Gatilho, HoraPrevisao, Limiares


def avaliar(horas: list[HoraPrevisao], limiares: Limiares, agora: datetime) -> list[Gatilho]:
    """Compara a previsão de agora até agora + janela_h com os limiares do talhão."""
    fim_janela = agora + timedelta(hours=limiares.janela_h)
    janela = [h for h in horas if agora <= h.ts < fim_janela]
    gatilhos: list[Gatilho] = []

    chuvosas = [h for h in janela if h.chuva_mm > 0]
    total = sum(h.chuva_mm for h in janela)
    if chuvosas and total >= limiares.chuva_mm:
        gatilhos.append(Gatilho(tipo="chuva", valor=total, limiar=limiares.chuva_mm,
                                inicio=chuvosas[0].ts, fim=chuvosas[-1].ts))

    ventosas = [h for h in janela if h.vento_kmh >= limiares.vento_kmh]
    if ventosas:
        gatilhos.append(Gatilho(tipo="vento", valor=max(h.vento_kmh for h in ventosas),
                                limiar=limiares.vento_kmh, inicio=ventosas[0].ts, fim=ventosas[-1].ts))

    secas = [h for h in janela if h.umidade_pct < limiares.umidade_min_pct]
    if secas:
        gatilhos.append(Gatilho(tipo="umidade", valor=min(h.umidade_pct for h in secas),
                                limiar=limiares.umidade_min_pct, inicio=secas[0].ts, fim=secas[-1].ts))
    return gatilhos
