# Changelog

All notable changes are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

## [Unreleased]

### Fixed
- Correcciones de la revisión: el aviso de bajados no se pierde si falla la lectura de la cola, un trabajo que falla antes del POST vuelve a `por_enviar`, el apagado del servidor MCP espera los envíos en curso (con tope) y `listar_biblioteca` controla cualquier error con el mensaje redactado.

---

## [0.1.0] — 2026-11-18

### Added
- Estructura del proyecto con `uv`, aislamiento de pruebas (`conftest.py`) y escritura atómica de archivos.
