from yti_cliente.vtt import contar_segmentos, parsear_vtt


def test_basico():
    v = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHola &amp; <c>adiós</c>\n"
    assert parsear_vtt(v) == [(1.0, "Hola & adiós")]


def test_horas_y_coma():
    assert parsear_vtt("WEBVTT\n\n01:02:03,500 --> 01:02:04,000\nx\n")[0][0] == 3723.5


def test_sin_horas():
    assert parsear_vtt("WEBVTT\n\n02:03.250 --> 02:04.000\nx\n")[0][0] == 123.25


def test_repetidas_auto():
    v = (
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nA\n\n"
        "00:00:02.000 --> 00:00:03.000\nA\nB\n\n"
        "00:00:03.000 --> 00:00:04.000\nB\nC\n"
    )
    assert [t for _, t in parsear_vtt(v)] == ["A", "B", "C"]


def test_crlf_y_vacio():
    assert contar_segmentos("WEBVTT\r\n\r\n") == 0


def test_crlf_con_cues():
    v = "WEBVTT\r\n\r\n00:00:01.000 --> 00:00:02.000 align:start\r\nhola\r\n\r\n"
    assert parsear_vtt(v) == [(1.0, "hola")]


def test_lineas_vacias_tras_limpiar():
    v = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<c></c>\n"
    assert contar_segmentos(v) == 0
