"""Autodesk Fusion — discovery only.

Fusion's API runs *inside* the Fusion process (Python/C++ scripts and add-ins). A generic external
Python process cannot call it directly; a real integration needs an add-in installed in Fusion
that exposes a local bridge. That add-in does not exist yet.
"""
from __future__ import annotations

from ._discovery_only import DiscoveryOnlyAdapter


class FusionAdapter(DiscoveryOnlyAdapter):
    name = "fusion"
    application = "Autodesk Fusion"
    mechanism = "planned: in-process Fusion add-in exposing a loopback bridge"
    licensing = "Autodesk account + Fusion subscription/personal licence; API use within Autodesk terms"
    planned_api = "Fusion add-in bridge"

    def _find(self):
        local = self.probe.env("LOCALAPPDATA") or ""
        pattern = local + r"\Autodesk\webdeploy\production\*\Fusion360.exe"
        hits = self.probe.glob(pattern)
        facts = {"pattern": pattern, "candidates": hits,
                 "running_pids": self.probe.running("Fusion360.exe")}
        if not hits:
            return None, None, facts
        return hits[-1], None, facts
