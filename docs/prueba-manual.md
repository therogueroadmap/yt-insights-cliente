# Prueba manual contra Render

> Requiere la autorización del usuario antes de gastar cuota; no refresca la caché de Gemini.

Esta prueba se hace a mano con claves reales y la API desplegada. Ninguna prueba automática la sustituye.

## Pasos

1. Ejecuta `yti config` con claves reales y verifica que las muestra enmascaradas.
2. Ejecuta `yti encolar <video ya presente en la caché del servidor> --solo-transcripcion`. La transcripción sale de la caché y no gasta Gemini ni Anthropic (aunque `X-Gemini-Key` es obligatoria). Verifica que el testigo se envió (`fuente_testigo: cliente`), ejecuta `yti esperar` y comprueba los archivos en la biblioteca y `indice.md`.
3. Repite el paso 2: debe avisar de duplicado sin enviar.
4. Pide un análisis con objetivo sobre el mismo video (gasta Anthropic, no Gemini). Verifica `analisis-<slug>.md` y el costo en el front matter.
5. Configura el cliente en un host MCP (Claude Desktop o Claude Code) y ejecuta `encolar_video`, `ver_cola`, `listar_biblioteca` y `leer_resultado`.
6. Mide los tiempos reales para calibrar la estimación (R6).
7. Revisa los logs del cliente: no debe aparecer ninguna clave.

## Nota

El tope diario y `CLAVES_NO_DISPONIBLES` no se prueban contra Render (gastarían trabajos o exigirían reiniciar el servicio); quedan cubiertos por las pruebas de unidad.
