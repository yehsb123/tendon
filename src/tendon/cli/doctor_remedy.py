"""Turning an import failure into something a reader can act on.

Separate from `doctor.py` so that the mapping from failure to advice can be tested
directly, without constructing an environment where torch is broken.
"""

from __future__ import annotations

__all__ = ["import_remedy"]


def import_remedy(exc: Exception) -> str:
    """What to do about a package that is installed but will not import.

    A stack trace tells a reader that something is broken. This tells them what to do,
    which is the only reason they ran the command.
    """
    message = str(exc)

    if "Visual C++" in message or "vc_redist" in message:
        return (
            "install the Microsoft Visual C++ Redistributable: "
            "https://aka.ms/vs/17/release/vc_redist.x64.exe"
        )
    if "DLL load failed" in message:
        return "a native dependency is missing or mismatched; reinstall torch"
    return 'reinstall torch, or run `python -c "import torch"` to see the full error'
