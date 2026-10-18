"""Model gateway: the user's own local language model as an optional planner - typed specs only, never code.

The model is the one the user runs on this PC with Strata (Qwen3.8-Flash-Next, the Coder build), reached over
Strata's OpenAI-compatible chat API on the loopback address (default http://127.0.0.1:8080/v1). Any other local
server with that API (llama.cpp, LM Studio, ...) works the same way.

Nothing leaves the PC: an address that is not this computer is refused unless the user sets
ASTRACAD_MODEL_ALLOW_REMOTE=1, and requests never go through a proxy. No key is stored here (an API key, when the
local server asks for one, is read from ASTRACAD_MODEL_API_KEY). The request asks for no tools ("tool_choice": none,
and never Strata's "strata_mcp" opt-in), so the model can only answer, not act.

Settings (environment variables):
    ASTRACAD_MODEL_PROVIDER    local | off                     (default local)
    ASTRACAD_MODEL_URL         the API's base URL              (default http://127.0.0.1:8080/v1)
    ASTRACAD_MODEL_NAME        the model name sent             (default qwen3.8-flash-next-coder-iq1_m)
    ASTRACAD_MODEL_THINKING    none | low | medium | high      (default low; Strata's reasoning_effort)
    ASTRACAD_MODEL_TIMEOUT_S   seconds to wait for an answer   (default 900)
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "qwen3.8-flash-next-coder-iq1_m"
THINKING = ("none", "low", "medium", "high")
THINKING_BUDGET = 2500          # tokens; with an 8K context the answer keeps the rest
START_HINT = "start the local model server (Strata: run-coder-iq1_m.bat) and wait until the model is loaded"


class ModelError(RuntimeError):
    """The model could not be reached or gave no usable answer; the reason is in the message."""


@dataclass
class Reply:
    text: str
    provider: str
    model: str
    meta: dict[str, Any] = field(default_factory=dict)


def _loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# direct connections only: a system proxy must never see the request (it would leave the PC)
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class LocalModel:
    """An OpenAI-compatible chat server on this PC - by default Strata serving Qwen3.8-Flash-Next."""
    name = "local"

    def __init__(self, url: str | None = None, model: str | None = None, thinking: str | None = None,
                 timeout_s: float | None = None):
        self.url = (url or os.environ.get("ASTRACAD_MODEL_URL") or DEFAULT_URL).strip().rstrip("/")
        self.model = (model or os.environ.get("ASTRACAD_MODEL_NAME") or DEFAULT_MODEL).strip()
        self.thinking = (thinking or os.environ.get("ASTRACAD_MODEL_THINKING") or "low").strip().lower()
        try:
            self.timeout_s = float(timeout_s or os.environ.get("ASTRACAD_MODEL_TIMEOUT_S") or 900)
        except ValueError:
            self.timeout_s = 900.0

    def problem(self) -> str | None:
        """Why this configuration may not be used; None when it may."""
        u = urllib.parse.urlsplit(self.url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return f"ASTRACAD_MODEL_URL must be an http address such as {DEFAULT_URL}, not {self.url!r}"
        if not _loopback(u.hostname) and os.environ.get("ASTRACAD_MODEL_ALLOW_REMOTE") != "1":
            return (f"{u.hostname} is not this PC; ASTraCAD sends requests only to a model on this computer "
                    "(ASTRACAD_MODEL_ALLOW_REMOTE=1 allows another address - then the request leaves the PC)")
        if self.thinking not in THINKING:
            return f"ASTRACAD_MODEL_THINKING must be one of {', '.join(THINKING)}, not {self.thinking!r}"
        return None

    def where(self) -> str:
        host = urllib.parse.urlsplit(self.url).hostname or ""
        return f"{self.url} ({'on this PC' if _loopback(host) else 'another computer'})"

    def _open(self, path: str, body: dict | None, timeout: float):
        headers = {"Content-Type": "application/json"}
        key = os.environ.get("ASTRACAD_MODEL_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
        return _OPENER.open(req, timeout=timeout)

    @staticmethod
    def _http_error(ex: urllib.error.HTTPError) -> str:
        raw = ex.read().decode("utf-8", "replace")
        try:
            msg = json.loads(raw)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            msg = raw[:300]
        hint = {401: " (the server wants an API key: set ASTRACAD_MODEL_API_KEY)",
                409: " (the model is busy with another request; ask again when it is done)",
                503: " (the model is still loading or restarting; ask again in a moment)"}.get(ex.code, "")
        return f"the local model server answered {ex.code}: {msg}{hint}"

    def available(self) -> tuple[bool, str]:
        bad = self.problem()
        if bad:
            return False, bad
        try:
            with self._open("/models", None, timeout=3) as r:
                doc = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            return False, self._http_error(ex)
        except (urllib.error.URLError, OSError, ValueError):
            return False, f"no model server answers at {self.url} - {START_HINT}"
        names = [str(m.get("id")) for m in (doc.get("data") or []) if isinstance(m, dict) and m.get("id")]
        return True, f"{', '.join(names[:3]) or self.model} at {self.where()}, thinking {self.thinking}"

    def complete(self, system: str, user: str) -> Reply:
        bad = self.problem()
        if bad:
            raise ModelError(bad)
        body: dict[str, Any] = {
            "model": self.model, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},   # Strata checks that the answer is one JSON object
            "tool_choice": "none",                          # no tools: the model answers, it does not act
            "reasoning_effort": self.thinking,
        }
        if self.thinking != "none":
            body["reasoning_budget_tokens"] = THINKING_BUDGET
        try:
            with self._open("/chat/completions", body, timeout=self.timeout_s) as r:
                doc = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as ex:
            raise ModelError(self._http_error(ex)) from None
        except TimeoutError:
            raise ModelError(f"the local model did not answer within {self.timeout_s:.0f} s") from None
        except (urllib.error.URLError, OSError) as ex:
            reason = getattr(ex, "reason", ex)
            raise ModelError(f"the local model at {self.url} was not reached ({reason}) - {START_HINT}") from None
        except ValueError:
            raise ModelError("the local model server sent something that is not JSON") from None
        try:
            choice = doc["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise ModelError(f"the local model server gave no answer: {json.dumps(doc)[:300]}") from None
        text = re.sub(r"<think>.*?</think>", "", str(msg.get("content") or ""), flags=re.S).strip()
        if choice.get("finish_reason") == "length":
            raise ModelError("the local model ran out of room before its answer was complete (context full); "
                             "try a shorter request or ASTRACAD_MODEL_THINKING=none")
        if not text:
            raise ModelError("the local model gave an empty answer")
        reasoning = str(msg.get("reasoning_content") or "")
        return Reply(text, self.name, str(doc.get("model") or self.model),
                     {"where": self.where(), "thinking": self.thinking, "usage": doc.get("usage") or {},
                      "finish_reason": choice.get("finish_reason"), "reasoning": reasoning[:20000]})


def provider():
    """The configured provider, or None when the model gateway is switched off."""
    choice = (os.environ.get("ASTRACAD_MODEL_PROVIDER") or "local").strip().lower()
    return None if choice == "off" else LocalModel()


def status() -> dict[str, Any]:
    p = provider()
    if p is None:
        return {"provider": None, "available": False, "detail": "switched off (ASTRACAD_MODEL_PROVIDER=off)"}
    ok, detail = p.available()
    return {"provider": p.name, "model": p.model, "url": p.url, "thinking": p.thinking, "available": ok,
            "detail": detail, "sends": f"the request text and ASTraCAD's fixed planning instructions, to {p.where()} "
                                       "- no drawings, models or files"}
