"""Configuración de pytest compartida.

Añade la raíz del repositorio y ``src/`` al path, y establece una credencial
exclusivamente de prueba para los módulos legacy que construyen la app al
importarse. Ningún valor de la máquina del desarrollador se usa en la suite.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

os.environ.setdefault("KIRO_GATEWAY_KEY", "test-only-gateway-key")

for path in (ROOT, SRC):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)
