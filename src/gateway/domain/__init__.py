"""Reglas de negocio puras del gateway.

Este paquete no importa `httpx`, `fastapi`, `sqlite3` ni lee `os.environ`:
todo lo que contiene es determinista y testeable sin red.
"""
