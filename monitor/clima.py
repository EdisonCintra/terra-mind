import math


def delta_t(temp_c: float, umidade_pct: float) -> float:
    """Delta T = temperatura menos bulbo úmido (Stull, 2011), arredondado a 1 casa."""
    rh = umidade_pct
    tw = (temp_c * math.atan(0.151977 * math.sqrt(rh + 8.313659))
          + math.atan(temp_c + rh)
          - math.atan(rh - 1.676331)
          + 0.00391838 * rh ** 1.5 * math.atan(0.023101 * rh)
          - 4.686035)
    return round(temp_c - tw, 1)


def inversao_provavel(temp_2m_c: float, temp_alto_c: float | None, vento_kmh: float,
                      nuvens_pct: float | None, dia: bool | None,
                      vento_max_kmh: float) -> bool | None:
    """Inversão térmica provável na hora. None quando falta dado para decidir.

    Com `temp_alto_c` (80-120 m) decide só por ela: True se está mais quente que a 2 m
    e o vento é menor que `vento_max_kmh`. Não mais quente → False (não None).
    Sem ela, usa a heurística: noite (`dia is False`), céu limpo (<30%) e vento fraco.
    """
    if temp_alto_c is not None:
        return temp_alto_c > temp_2m_c and vento_kmh < vento_max_kmh
    if dia is None or nuvens_pct is None:
        return None
    return dia is False and nuvens_pct < 30 and vento_kmh < vento_max_kmh
