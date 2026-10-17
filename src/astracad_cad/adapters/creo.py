"""PTC Creo Parametric — discovery only.

Documented automation routes (to be evaluated against an installed, licensed Creo): Creo
TOOLKIT (C, needs a TOOLKIT licence), J-Link/Object TOOLKIT Java & C++, Creo Web.Link, and the
VB API (COM, `pfcls`). Which of these is present depends on the installation options and licence.
"""
from __future__ import annotations

import re

from ._discovery_only import DiscoveryOnlyAdapter


class CreoAdapter(DiscoveryOnlyAdapter):
    name = "creo"
    application = "PTC Creo Parametric"
    mechanism = "planned: Creo VB API (pfcls COM) or Object TOOLKIT — not yet selected"
    licensing = "Creo licence; TOOLKIT development requires a separate PTC licence"
    planned_api = "Creo VB API / TOOLKIT"

    def _find(self):
        hits = self.probe.glob(r"C:\Program Files\PTC\Creo *\Parametric\bin\parametric.exe")
        facts = {"candidates": hits,
                 "vb_api_registered": self.probe.registry_value("HKCR", r"pfcls.pfcAsyncConnection",
                                                                "") is not None}
        if not hits:
            return None, None, facts
        path = hits[-1]
        m = re.search(r"Creo (\d+(?:\.\d+)*)", path)
        return path, m.group(1) if m else None, facts
