"""Environment probes used by adapter discovery.

All Windows access goes through this module so adapters stay testable on any OS: tests inject a
``Probe`` with canned answers; production uses ``SystemProbe``. Nothing here starts an
application — discovery is read-only.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


class Probe:
    platform: str = sys.platform

    def registry_subkeys(self, hive: str, path: str) -> list[str]:
        return []

    def registry_value(self, hive: str, path: str, name: str) -> str | None:
        return None

    def glob(self, pattern: str) -> list[str]:
        return []

    def exists(self, path: str) -> bool:
        return False

    def running(self, image_name: str) -> list[int]:
        return []

    def which(self, exe: str) -> str | None:
        return None

    def env(self, name: str) -> str | None:
        return None

    def run(self, argv: list[str], timeout: float, cwd: str | None = None
            ) -> subprocess.CompletedProcess:
        raise FileNotFoundError(argv[0])


class SystemProbe(Probe):
    def __init__(self) -> None:
        self.platform = sys.platform

    def _hive(self, hive: str):
        import winreg
        return {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER,
                "HKCR": winreg.HKEY_CLASSES_ROOT}[hive]

    def registry_subkeys(self, hive: str, path: str) -> list[str]:
        if self.platform != "win32":
            return []
        import winreg
        try:
            with winreg.OpenKey(self._hive(hive), path) as k:
                out, i = [], 0
                while True:
                    try:
                        out.append(winreg.EnumKey(k, i))
                    except OSError:
                        return out
                    i += 1
        except OSError:
            return []

    def registry_value(self, hive: str, path: str, name: str) -> str | None:
        if self.platform != "win32":
            return None
        import winreg
        try:
            with winreg.OpenKey(self._hive(hive), path) as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            return None

    def glob(self, pattern: str) -> list[str]:
        return sorted(glob.glob(os.path.expandvars(pattern)))

    def exists(self, path: str) -> bool:
        return Path(os.path.expandvars(path)).exists()

    def running(self, image_name: str) -> list[int]:
        if self.platform != "win32":
            return []
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV",
                                  "/NH"], capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        pids = []
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) > 1 and parts[0].lower() == image_name.lower():
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
        return pids

    def which(self, exe: str) -> str | None:
        return shutil.which(exe)

    def env(self, name: str) -> str | None:
        return os.environ.get(name)

    def run(self, argv: list[str], timeout: float, cwd: str | None = None
            ) -> subprocess.CompletedProcess:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                              encoding="utf-8", errors="replace", shell=False)


@dataclass
class FakeProbe(Probe):
    """Deterministic probe for tests."""
    platform: str = "win32"
    subkeys: dict[str, list[str]] = field(default_factory=dict)
    values: dict[str, str] = field(default_factory=dict)
    files: set[str] = field(default_factory=set)
    globs: dict[str, list[str]] = field(default_factory=dict)
    processes: dict[str, list[int]] = field(default_factory=dict)
    environ: dict[str, str] = field(default_factory=dict)
    responder: object = None   # callable(argv) -> CompletedProcess
    calls: list[list[str]] = field(default_factory=list)

    def registry_subkeys(self, hive, path):
        return self.subkeys.get(f"{hive}\\{path}", [])

    def registry_value(self, hive, path, name):
        return self.values.get(f"{hive}\\{path}\\{name}")

    def glob(self, pattern):
        return self.globs.get(pattern, [])

    def exists(self, path):
        return path in self.files

    def running(self, image_name):
        return self.processes.get(image_name.lower(), [])

    def which(self, exe):
        return None

    def env(self, name):
        return self.environ.get(name)

    def run(self, argv, timeout, cwd=None):
        self.calls.append(list(argv))
        if self.responder is None:
            raise FileNotFoundError(argv[0])
        return self.responder(argv)
