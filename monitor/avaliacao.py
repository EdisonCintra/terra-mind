from datetime import datetime, timedelta

from monitor.modelos import Gatilho, HoraPrevisao, Limiares


def avaliar(horas: list[HoraPrevisao], limiares: Limiares, agora: datetime) -> list[Gatilho]:
    """Compara a previsão de agora até agora + janela_h com os limiares do talhão."""
    fim_janela = agora + timedelta(hours=limiares.janela_h)
    janela = [h for h in horas if agora <= h.ts < fim_janela]
    gatilhos: list[Gatilho] = []

    # Campo None (sem dado na hora) nunca conta como 0: a hora só fica fora daquela checagem.
    chuvosas = [h for h in janela if h.chuva_mm is not None and h.chuva_mm > 0]
    total = sum(h.chuva_mm for h in chuvosas)
    if chuvosas and total >= limiares.chuva_mm:
        gatilhos.append(Gatilho(tipo="chuva", valor=total, limiar=limiares.chuva_mm,
                                inicio=chuvosas[0].ts, fim=chuvosas[-1].ts))

    ventosas = [h for h in janela if h.vento_kmh is not None and h.vento_kmh >= limiares.vento_kmh]
    if ventosas:
        gatilhos.append(Gatilho(tipo="vento", valor=max(h.vento_kmh for h in ventosas),
                                limiar=limiares.vento_kmh, inicio=ventosas[0].ts, fim=ventosas[-1].ts))

    secas = [h for h in janela if h.umidade_pct is not None and h.umidade_pct < limiares.umidade_min_pct]
    if secas:
        gatilhos.append(Gatilho(tipo="umidade", valor=min(h.umidade_pct for h in secas),
                                limiar=limiares.umidade_min_pct, inicio=secas[0].ts, fim=secas[-1].ts))

    # Campos opcionais None (sem dado no Open-Meteo) são ignorados.
    # Vento e Delta T abaixo do mínimo não abrem incidente (vão ao risco amarelo).
    rajadas = [h for h in janela if h.rajada_kmh is not None and h.rajada_kmh >= limiares.rajada_kmh]
    if rajadas:
        gatilhos.append(Gatilho(tipo="rajada", valor=max(h.rajada_kmh for h in rajadas),
                                limiar=limiares.rajada_kmh, inicio=rajadas[0].ts, fim=rajadas[-1].ts))

    secos = [h for h in janela if h.delta_t_c is not None and h.delta_t_c > limiares.delta_t_max_c]
    if secos:
        gatilhos.append(Gatilho(tipo="delta_t", valor=max(h.delta_t_c for h in secos),
                                limiar=limiares.delta_t_max_c, inicio=secos[0].ts, fim=secos[-1].ts))

    invertidas = [h for h in janela if h.inversao is True]
    if invertidas:
        gatilhos.append(Gatilho(tipo="inversao", valor=1, limiar=0,
                                inicio=invertidas[0].ts, fim=invertidas[-1].ts))
    return gatilhos
