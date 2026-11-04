"""Model providers: pluggable LLM backends for the writer agent.

Configured via models.json next to the .fab file (or in the project root):

{
  "default": "claude-cli",
  "providers": {
    "claude-api": {"type": "anthropic", "model": "claude-opus-5"},
    "claude-cli": {"type": "claude-cli", "model": "opus"},
    "gpt":        {"type": "openai", "base_url": "https://api.openai.com/v1",
                   "model": "gpt-5.2", "api_key_env": "OPENAI_API_KEY"},
    "ollama":     {"type": "openai", "base_url": "http://localhost:11434/v1",
                   "model": "llama3.3"}
  }
}

Provider types:
  anthropic   — Anthropic SDK (pip install anthropic); credentials from
                ANTHROPIC_API_KEY or an `ant auth login` profile
  claude-cli  — the locally installed `claude` console tool in print mode
                (no API key needed, uses the user's Claude Code auth)
  codex-cli   — local `codex exec`, using the user's existing Codex login
  openai      — any OpenAI-compatible chat/completions endpoint over raw HTTP
                (OpenAI, OpenRouter, Gemini's compat endpoint, Ollama, ...);
                the token comes from the env var named in api_key_env
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .i18n import t

GENERATION_TIMEOUT = 900  # seconds per call; fiction scenes can take a while
PROBE_TIMEOUT = 1.5       # seconds to wait for a keyless endpoint in status()
PROBE_TTL_OK = 60.0       # how long a reachable verdict stays fresh
PROBE_TTL_FAIL = 10.0     # shorter, so a server started just now shows up quickly

# base_url -> (expires_at, reason); shared across Registry instances, which the
# server rebuilds on every request — a per-provider cache would never hit.
_PROBE_CACHE: dict[str, tuple[float, str]] = {}
_PROBE_LOCK = threading.Lock()


class ProviderError(RuntimeError):
    pass


class Provider:
    type = "?"

    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg
        self.model = cfg.get("model")

    def generate(self, system: str, prompt: str, effort: str = "high") -> str:
        raise NotImplementedError

    def status(self) -> dict:
        """{"available": bool, "reason": str} — why generation would fail, if it would."""
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name, "type": self.type, "model": self.model, **self.status()}


class AnthropicProvider(Provider):
    type = "anthropic"

    def __init__(self, name: str, cfg: dict):
        super().__init__(name, cfg)
        self.model = self.model or "claude-opus-5"

    def status(self) -> dict:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return {"available": False, "reason": t("provider.sdk_missing")}
        key_env = self.cfg.get("api_key_env", "ANTHROPIC_API_KEY")
        if key_env and not os.environ.get(key_env):
            # not fatal: the SDK also resolves `ant auth login` profiles
            return {"available": True, "reason": t("provider.key_profile_hint", env=key_env)}
        return {"available": True, "reason": ""}

    def generate(self, system: str, prompt: str, effort: str = "high") -> str:
        import anthropic
        client = anthropic.Anthropic()
        kwargs = {}
        # server-side refusal fallbacks exist only on the top-tier models
        if self.model.startswith(("claude-opus-5", "claude-fable-5")):
            kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        with client.beta.messages.stream(
            model=self.model,
            max_tokens=64000,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}] if system else anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "refusal":
            details = response.stop_details
            raise ProviderError(
                t("provider.refused_cat", cat=details.category) if details else t("provider.refused")
            )
        return "".join(b.text for b in response.content if b.type == "text")


class ClaudeCLIProvider(Provider):
    """The `claude` console tool in headless print mode — no API key needed."""

    type = "claude-cli"

    def status(self) -> dict:
        if not shutil.which("claude"):
            return {"available": False, "reason": t("provider.cli_missing")}
        return {"available": True, "reason": ""}

    def generate(self, system: str, prompt: str, effort: str = "high") -> str:
        # --output-format json separates the answer from any noise reliably
        cmd = ["claude", "-p", "--output-format", "json", "--max-turns", "1"]
        if self.model:
            cmd += ["--model", self.model]
        if system:
            cmd += ["--append-system-prompt", system]
        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                timeout=self.cfg.get("timeout", GENERATION_TIMEOUT),
            )
        except FileNotFoundError:
            raise ProviderError(t("provider.cli_missing"))
        except subprocess.TimeoutExpired:
            raise ProviderError(t("provider.cli_timeout"))
        if proc.returncode != 0:
            # drop settings-validation warnings — they are noise, not the failure
            lines = [ln for ln in (proc.stderr or "").splitlines()
                     if ln.strip() and "Permission allow rule" not in ln]
            detail = " / ".join(lines) or proc.stdout.strip() or t("provider.unknown_error")
            if "authenticate" in detail.lower() or "oauth" in detail.lower():
                detail += t("provider.cli_login_hint")
            raise ProviderError(t("provider.cli_error", detail=detail))
        raw = proc.stdout.strip()
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                if data.get("is_error"):
                    raise ProviderError(t("provider.cli_error",
                                          detail=str(data.get("result") or data.get("subtype") or raw)[:400]))
                text = str(data.get("result") or "").strip()
            else:
                text = raw
        except json.JSONDecodeError:
            text = raw  # older CLI versions may answer in plain text
        if not text:
            raise ProviderError(t("provider.cli_empty"))
        return text


class CodexCLIProvider(Provider):
    """Text generation through an authenticated local Codex CLI (no SDK)."""

    type = "codex-cli"

    def status(self) -> dict:
        if not shutil.which("codex"):
            return {"available": False, "reason": t("provider.codex_missing")}
        # Presence is cheap to check on every UI refresh; auth is checked by exec.
        return {"available": True, "reason": t("provider.codex_auth_note")}

    def generate(self, system: str, prompt: str, effort: str = "high") -> str:
        # Use a fresh directory and disable repository instructions so the
        # writer receives the supplied text rather than the current checkout.
        with tempfile.TemporaryDirectory(prefix="fabula-codex-") as directory:
            output = Path(directory) / "answer.txt"
            cmd = ["codex", "exec", "--ephemeral", "--ignore-user-config",
                   "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
                   "-c", "approval_policy=\"never\"",
                   "-c", "features.shell_tool=false", "-c", "project_doc_max_bytes=0",
                   "-c", "model_reasoning_effort=" + json.dumps(effort),
                   "-c", "developer_instructions=" + json.dumps(system + "\n\n" +
                       "Return only the requested text. Do not use tools or inspect files.", ensure_ascii=False),
                   "--output-last-message", str(output)]
            if self.model:
                cmd += ["--model", self.model]
            cmd.append("-")
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                      encoding="utf-8", cwd=directory,
                                      timeout=self.cfg.get("timeout", GENERATION_TIMEOUT))
            except FileNotFoundError:
                raise ProviderError(t("provider.codex_missing")) from None
            except subprocess.TimeoutExpired:
                raise ProviderError(t("provider.codex_timeout")) from None
            except OSError as e:
                raise ProviderError(t("provider.codex_error", detail=str(e))) from e
            if proc.returncode:
                detail = (proc.stderr or proc.stdout or t("provider.unknown_error")).strip()[-1200:]
                raise ProviderError(t("provider.codex_error", detail=detail))
            # Never return event logs/stdout as fiction or a JSON report.
            answer = output.read_text(encoding="utf-8").strip() if output.exists() else ""
            if not answer:
                raise ProviderError(t("provider.codex_empty"))
            return answer


class OpenAICompatProvider(Provider):
    """Any OpenAI-compatible /chat/completions endpoint, over raw HTTP (no deps)."""

    type = "openai"

    def __init__(self, name: str, cfg: dict):
        super().__init__(name, cfg)
        self.base_url = (cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.key_env = cfg.get("api_key_env")
        # Keyless profiles are local servers by convention (ollama, llama.cpp, LM Studio):
        # there is no token to check, so without a probe status() could only ever say "ok".
        # `"probe": true|false` in the profile overrides the default either way.
        self.probe = bool(cfg.get("probe", not self.key_env))

    def _reach(self) -> str:
        """Empty string if the endpoint answers, else why it does not. Cached by base_url."""
        now = time.monotonic()
        with _PROBE_LOCK:
            hit = _PROBE_CACHE.get(self.base_url)
            if hit and hit[0] > now:
                return hit[1]
        req = urllib.request.Request(f"{self.base_url}/models", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT):
                reason = ""
        except urllib.error.HTTPError:
            reason = ""  # 401/404 still means something is listening and speaking HTTP
        except OSError:  # URLError, socket timeouts, DNS failures
            reason = t("provider.unreachable", url=self.base_url)
        with _PROBE_LOCK:
            _PROBE_CACHE[self.base_url] = (
                now + (PROBE_TTL_OK if not reason else PROBE_TTL_FAIL), reason)
        return reason

    def status(self) -> dict:
        if not self.model:
            return {"available": False, "reason": t("provider.no_model")}
        if self.key_env and not os.environ.get(self.key_env):
            return {"available": False, "reason": t("provider.no_token", env=self.key_env)}
        if self.probe:
            reason = self._reach()
            if reason:
                return {"available": False, "reason": reason}
        return {"available": True, "reason": ""}

    def generate(self, system: str, prompt: str, effort: str = "high") -> str:
        messages = ([{"role": "system", "content": system}] if system else []) \
                   + [{"role": "user", "content": prompt}]
        payload = {"model": self.model, "messages": messages}
        payload.update(self.cfg.get("extra_params", {}))
        headers = {"Content-Type": "application/json"}
        if self.key_env:
            token = os.environ.get(self.key_env)
            if not token:
                raise ProviderError(t("provider.no_token", env=self.key_env))
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.get("timeout", GENERATION_TIMEOUT)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            raise ProviderError(t("provider.http_error", code=e.code, url=self.base_url, detail=detail))
        except urllib.error.URLError as e:
            raise ProviderError(t("provider.no_connection", url=self.base_url, reason=e.reason))
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError(t("provider.bad_response", body=json.dumps(data)[:300]))
        if not content:
            raise ProviderError(t("provider.empty_response"))
        return content


_TYPES = {
    "anthropic": AnthropicProvider,
    "claude-cli": ClaudeCLIProvider,
    "codex-cli": CodexCLIProvider,
    "openai": OpenAICompatProvider,
}

DEFAULT_CONFIG = {
    "default": "claude-cli",
    "providers": {
        "codex-cli": {"type": "codex-cli", "timeout": 900},
        "claude-cli": {"type": "claude-cli", "model": "opus"},
        "claude-api": {"type": "anthropic", "model": "claude-opus-5"},
        "gpt": {"type": "openai", "base_url": "https://api.openai.com/v1",
                "model": "gpt-5.2", "api_key_env": "OPENAI_API_KEY"},
        "gemini": {"type": "openai",
                   "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                   "model": "gemini-2.5-pro", "api_key_env": "GEMINI_API_KEY"},
        "ollama": {"type": "openai", "base_url": "http://localhost:11434/v1",
                   "model": "llama3.3"},
    },
}


class Registry:
    def __init__(self, config: dict, source: Path | None = None):
        self.source = source
        self.default = config.get("default")
        self.profiles: dict[str, Provider] = {}
        for name, cfg in (config.get("providers") or {}).items():
            cls = _TYPES.get(cfg.get("type"))
            if cls:
                self.profiles[name] = cls(name, cfg)
        if self.default not in self.profiles and self.profiles:
            self.default = next(iter(self.profiles))

    def get(self, name: str | None = None) -> Provider:
        name = name or self.default
        if not name or name not in self.profiles:
            raise ProviderError(t("provider.unknown_profile", name=name, list=sorted(self.profiles)))
        return self.profiles[name]

    def describe(self) -> list[dict]:
        return [{**p.describe(), "default": name == self.default}
                for name, p in self.profiles.items()]


def find_config_path(near: Path | None = None) -> Path | None:
    """models.json next to the given file/dir, one level up, then in cwd."""
    candidates = []
    if near:
        base = near if near.is_dir() else near.parent
        candidates += [base / "models.json", base.parent / "models.json"]
    candidates.append(Path("models.json"))
    for path in candidates:
        if path.exists():
            return path
    return None


def load_registry(near: Path | None = None) -> Registry:
    path = find_config_path(near)
    if path:
        return Registry(json.loads(path.read_text(encoding="utf-8")), source=path)
    return Registry(DEFAULT_CONFIG)


_NAME_RE = re.compile(r"^[\w-]+$")
_ENV_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def validate_profile(name: str, cfg, field_prefix: str = "") -> list[dict]:
    """Structured, machine-readable errors — same philosophy as the compiler."""
    errors = []

    def err(field: str, message: str):
        errors.append({"field": f"{field_prefix}{field}", "message": message})

    if not name or not _NAME_RE.match(name):
        err("name", f"некоректна назва профілю {name!r} — літери/цифри/дефіс, без пробілів")
    if not isinstance(cfg, dict):
        err("", "профіль має бути об'єктом")
        return errors
    ptype = cfg.get("type")
    if ptype not in _TYPES:
        err("type", f"невідомий тип {ptype!r}; дозволені: {sorted(_TYPES)}")
        return errors
    if ptype == "openai":
        base_url = cfg.get("base_url")
        if not base_url or not str(base_url).startswith(("http://", "https://")):
            err("base_url", "base_url має починатися з http:// або https://")
        if not cfg.get("model"):
            err("model", "для openai-профілю поле model обов'язкове")
    if ptype == "anthropic" and not cfg.get("model"):
        err("model", "для anthropic-профілю поле model обов'язкове")
    key_env = cfg.get("api_key_env")
    if key_env is not None and (not isinstance(key_env, str) or not _ENV_RE.match(key_env)):
        err("api_key_env", "api_key_env — ім'я змінної середовища, напр. OPENAI_API_KEY")
    timeout = cfg.get("timeout")
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
        err("timeout", "timeout — додатне число секунд")
    return errors


def validate_config(config) -> list[dict]:
    if not isinstance(config, dict):
        return [{"field": "", "message": "конфіг має бути об'єктом"}]
    errors = []
    providers = config.get("providers")
    if not isinstance(providers, dict) or not providers:
        return [{"field": "providers", "message": "потрібен хоча б один профіль"}]
    for name, cfg in providers.items():
        errors += validate_profile(name, cfg, field_prefix=f"providers.{name}.")
    default = config.get("default")
    if not default:
        errors.append({"field": "default", "message": "познач один профіль як типовий"})
    elif default not in providers:
        errors.append({"field": "default",
                       "message": f"типовий профіль '{default}' не існує серед профілів"})
    return errors


def build_provider(name: str, cfg: dict) -> Provider:
    """Construct a provider from an (already validated) profile config."""
    return _TYPES[cfg["type"]](name, cfg)
