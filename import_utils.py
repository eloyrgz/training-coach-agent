"""Import helpers for bundled submodules used by agent tools."""

import sys
from pathlib import Path


def ensure_submodule_importable(submodule_name: str) -> None:
    path = str(Path(__file__).resolve().parent / submodule_name)
    if path not in sys.path:
        sys.path.insert(0, path)