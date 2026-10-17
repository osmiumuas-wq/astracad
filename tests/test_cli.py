"""The command surface on the machine that actually runs it: a Windows console is not UTF-8."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(argv, env_extra=None):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), **(env_extra or {}))
    return subprocess.run([sys.executable, "-m", "astracad_inspect", *argv],
                          cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


def test_doctor_reports_the_iso_286_route_as_loaded_and_names_its_scope():
    """`doctor` is the capability statement an operator reads before trusting a plan. Fit classes
    resolve from the published table now, so a blanket "NOT implemented" for that route understates
    the build; the line has to carry the scope actually loaded. 3-400 mm is the range the published
    deviation tables state, not a number this build computes."""
    out = _run(["doctor"], {"PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0, out.stderr
    line = next(l for l in out.stdout.splitlines() if "iso-286-fits" in l)
    assert "NOT implemented" not in line, line
    assert "ISO 286-2" in line, line
    assert "3-400 mm" in line, line


def test_the_selftest_survives_a_non_unicode_console():
    """A cp1252 console cannot encode the diameter sign. A check that cannot be printed still has
    to run and report, rather than turning a passing selftest into a failed command."""
    result = _run(["selftest"], {"PYTHONIOENCODING": "cp1252"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "selftest checks passed" in result.stdout
