def test_ejecutor_reexporta_errorlocal_y_limites():
    from yti_cliente import ejecutor, errores, limites
    assert ejecutor.ErrorLocal is errores.ErrorLocal
    assert ejecutor.MAX_OBJETIVO is limites.MAX_OBJETIVO
    assert (limites.MAX_OBJETIVO, limites.MAX_NOMBRE_PROYECTO, limites.MAX_NOTA) == (1000, 100, 4000)
    e = errores.ErrorLocal("X", "mensaje")
    assert (e.codigo, e.mensaje, str(e)) == ("X", "mensaje", "X: mensaje")
