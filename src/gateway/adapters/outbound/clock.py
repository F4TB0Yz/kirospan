"""Reloj real, implementación por defecto del puerto `Clock`."""

import time


class SystemClock:
    """Devuelve el tiempo de pared en segundos."""

    def now(self) -> float:
        return time.time()
