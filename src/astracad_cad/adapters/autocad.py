"""Autodesk AutoCAD — discovery only.

Documented routes: ActiveX/COM (`AutoCAD.Application.<n>`), AutoLISP, and the managed .NET API
(in-process). These are 2D-drawing and entity automation; AutoCAD is not treated as a parametric
solid modeller by this adapter. AutoCAD LT has no ActiveX/AutoLISP automation on most versions.
"""
from __future__ import annotations

from ._discovery_only import DiscoveryOnlyAdapter


class AutoCadAdapter(DiscoveryOnlyAdapter):
    name = "autocad"
    application = "Autodesk AutoCAD"
    mechanism = "planned: ActiveX/COM via AutoCAD.Application"
    licensing = "AutoCAD (not LT) licence for ActiveX/AutoLISP automation"
    planned_api = "AutoCAD ActiveX/COM"

    def _find(self):
        releases = self.probe.registry_subkeys("HKLM", r"SOFTWARE\Autodesk\AutoCAD")
        progid = self.probe.registry_value("HKCR", r"AutoCAD.Application\CLSID", "")
        exes = self.probe.glob(r"C:\Program Files\Autodesk\AutoCAD *\acad.exe")
        facts = {"registry_releases": releases, "progid_clsid": progid, "candidates": exes}
        if not exes:
            return None, None, facts
        return exes[-1], exes[-1].split("AutoCAD ")[-1].split("\\")[0], facts
