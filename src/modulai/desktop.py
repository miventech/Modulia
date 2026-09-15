"""Punto de entrada de escritorio para la distribución ejecutable de ModulAI."""

from __future__ import annotations

import sys

from modulai.__main__ import main


if len(sys.argv) == 1:
    sys.argv.append("--web")

main()
