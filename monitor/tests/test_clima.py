from monitor.clima import delta_t, inversao_provavel


def test_delta_t_stull():
    assert delta_t(20, 50) == 6.3
    assert delta_t(30, 30) == 11.6
    assert delta_t(25, 90) == 1.3


def test_inversao_com_temperatura_em_altura():
    assert inversao_provavel(22, 23.5, 3, None, None, 6) is True
    assert inversao_provavel(22, 23.5, 10, None, None, 6) is False


def test_inversao_com_temperatura_em_altura_nao_mais_quente():
    # Sem inversão: a temperatura em altura não supera a do solo, mesmo com vento fraco.
    assert inversao_provavel(22, 21.0, 2, None, None, 6) is False


def test_inversao_heuristica_madrugada_limpa():
    assert inversao_provavel(18, None, 2, 10, False, 6) is True
    assert inversao_provavel(18, None, 2, 10, True, 6) is False


def test_inversao_sem_dado():
    assert inversao_provavel(18, None, 2, None, None, 6) is None
