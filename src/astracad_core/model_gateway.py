"""Model gateway: a language model as an optional planner - opt-in, typed specs only, never code.

Two ways to reach Google Gemini (constitution: an external provider only with the user's explicit opt-in):

- ``gemini-cli`` - the Gemini CLI signed in with the user's Google account, so a Google AI Pro plan's quota is
  used and no key is stored here. Run as a subprocess in headless mode (``-p`` + ``--output-format json``),
  in an empty scratch folder, read-only (``--approval-mode plan`` where the CLI has it), with the request on
  standard input - never on the command line, where text could be read as arguments.
- ``gemini-api`` - the Gemini API with a key the user created in Google AI Studio, read from the environment
  variable GEMINI_API_KEY (never written to a file here) and sent as a header, never in the URL.

What leaves the PC: the request text and ASTraCAD's fixed planning instructions. No drawing, model, file or
path is sent. Choose with ASTRACAD_MODEL_PROVIDER = gemini-cli | gemini-api | off (default: the CLI when
installed, else the API when a key is set, else off); the model with ASTRACAD_GEMINI_MODEL.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


class ModelError(RuntimeError):
    """The model could not be reached or gave no usable answer; the reason is in the message."""


@dataclass
class Reply:
    text: str
    provider: str
    model: str
    meta: dict[str, Any] = field(default_factory=dict)


class GeminiCLI:
    name = "gemini-cli"
    # the -p text is appended to standard input by the CLI; the request itself only ever travels on stdin
    INSTRUCTION = "Follow the instructions above exactly. Reply with the single JSON object only."

    def __init__(self, model: str | None = None, exe: str | None = None, timeout_s: float = 240.0):
        self.model = model or os.environ.get("ASTRACAD_GEMINI_MODEL") or "pro"
        self._exe = exe
        self.timeout_s = timeout_s
        self._help: str | None = None

    def exe(self) -> str | None:
        return self._exe or shutil.which("gemini")

    def available(self) -> tuple[bool, str]:
        exe = self.exe()
        if not exe:
            return False, ("the Gemini CLI is not installed: npm install -g @google/gemini-cli, then run `gemini` "
                           "once and choose 'Login with Google' (your Google AI Pro plan's quota is then used)")
        return True, f"Gemini CLI at {exe}, model '{self.model}' (signed in with your Google account)"

    def _supports(self, text: str) -> bool:
        if self._help is None:
            try:
                cp = subprocess.run([self.exe(), "--help"], capture_output=True, text=True, timeout=60,
                                    encoding="utf-8", errors="replace")
                self._help = (cp.stdout or "") + (cp.stderr or "")
            except (OSError, subprocess.SubprocessError):
                self._help = ""
        return text in self._help

    def complete(self, prompt: str) -> Reply:
        exe = self.exe()
        if not exe:
            raise ModelError(self.available()[1])
        argv = [exe, "-p", self.INSTRUCTION, "--output-format", "json", "-m", self.model]
        if self._supports("--approval-mode") and self._supports("plan"):
            argv += ["--approval-mode", "plan"]                  # read-only: no edits, no shell
        with tempfile.TemporaryDirectory(prefix="astracad_gemini_") as cwd:   # nothing here to read
            try:
                cp = subprocess.run(argv, input=prompt, capture_output=True, text=True, cwd=cwd,
                                    timeout=self.timeout_s, encoding="utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                raise ModelError(f"the Gemini CLI did not answer within {self.timeout_s:.0f} s") from None
            except OSError as ex:
                raise ModelError(f"the Gemini CLI could not be started: {ex}") from None
        out = (cp.stdout or "").strip()
        try:
            doc = json.loads(out[out.index("{"):]) if "{" in out else {}
        except ValueError:
            doc = {}
        if doc.get("error"):
            err = doc["error"]
            raise ModelError(f"Gemini CLI error: {err.get('message', err) if isinstance(err, dict) else err}")
        if cp.returncode != 0 or "response" not in doc:
            hint = " (not signed in? run `gemini` once and choose Login with Google)" if cp.returncode in (1, 41) else ""
            raise ModelError(f"the Gemini CLI exited with {cp.returncode}{hint}: {(cp.stderr or out)[-400:]}")
        return Reply(str(doc["response"]), self.name, self.model, {"stats": doc.get("stats") or {}})


class GeminiAPI:
    name = "gemini-api"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, model: str | None = None, timeout_s: float = 120.0):
        self.model = model or os.environ.get("ASTRACAD_GEMINI_MODEL") or "gemini-2.5-pro"
        self.timeout_s = timeout_s

    def available(self) -> tuple[bool, str]:
        if not os.environ.get("GEMINI_API_KEY"):
            return False, "no GEMINI_API_KEY in the environment (a key from Google AI Studio; not the AI Pro plan)"
        return True, f"Gemini API, model '{self.model}' (key from the environment)"

    def complete(self, prompt: str) -> Reply:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ModelError(self.available()[1])
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
        req = urllib.request.Request(self.URL.format(model=self.model), data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key},
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                doc = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            detail = ex.read().decode("utf-8", "replace")[:300]
            raise ModelError(f"Gemini API answered {ex.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, ValueError) as ex:
            raise ModelError(f"Gemini API not reached: {ex}") from None
        try:
            text = "".join(p.get("text", "") for p in doc["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError):
            raise ModelError(f"Gemini API gave no answer: {json.dumps(doc)[:300]}") from None
        return Reply(text, self.name, self.model, {"usage": doc.get("usageMetadata") or {}})


def provider():
    """The configured provider, or None when the model gateway is off or nothing is set up."""
    choice = (os.environ.get("ASTRACAD_MODEL_PROVIDER") or "auto").strip().lower()
    if choice == "off":
        return None
    if choice == "gemini-cli":
        return GeminiCLI()
    if choice == "gemini-api":
        return GeminiAPI()
    cli = GeminiCLI()
    if cli.available()[0]:
        return cli
    api = GeminiAPI()
    return api if api.available()[0] else None


def status() -> dict[str, Any]:
    choice = (os.environ.get("ASTRACAD_MODEL_PROVIDER") or "auto").strip().lower()
    if choice == "off":
        return {"provider": None, "available": False, "detail": "switched off (ASTRACAD_MODEL_PROVIDER=off)"}
    p = provider()
    if p is None:
        return {"provider": None, "available": False,
                "detail": GeminiCLI().available()[1] + "; or set GEMINI_API_KEY for the Gemini API"}
    ok, detail = p.available()
    return {"provider": p.name, "model": p.model, "available": ok, "detail": detail,
            "sends": "the request text and ASTraCAD's fixed planning instructions - no drawings, models or files"}
