"""Paquete del gateway de Kiro organizado en capas.

El nombre es `gateway` y no `kiro_gateway` para no colisionar con el módulo
`kiro_gateway.py` de la raíz, que se conserva como shim de compatibilidad para
los tests legacy.

Capas, con las dependencias apuntando hacia dentro:

- `domain`: reglas puras, sin frameworks ni E/S.
- `protocols`: traducción entre los formatos OpenAI, Anthropic y Kiro.
- `application`: casos de uso, dependen solo de los puertos.
- `adapters`: FastAPI, httpx y SQLite.
"""
