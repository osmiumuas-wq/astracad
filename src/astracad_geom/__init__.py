"""OpenCascade, ASTraCAD's second CAD kernel (docs/engineering/part-language-v2.md).

The same typed part spec that SOLIDWORKS builds through `mac` is built here, independently, so the two kernels can be
compared: a part counts as verified only when they agree. Also used for previews and for STEP output while SOLIDWORKS
is closed. OpenCascade (`cadquery-ocp`) is optional: without it, `available()` is False and callers report the
second-kernel checks as not verifiable, never as passed.
"""
from __future__ import annotations


def available() -> bool:
    try:
        import OCP  # noqa: F401
    except ImportError:
        return False
    return True
