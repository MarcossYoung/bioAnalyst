import os
import json
import re
import sys
import time
import requests
from dotenv import load_dotenv; load_dotenv()
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from anthropic import (
    Anthropic,
    APIConnectionError as AnthropicAPIConnectionError,
    APIStatusError as AnthropicAPIStatusError,
    RateLimitError as AnthropicRateLimitError,
)
from openai import (
    OpenAI,
    APIConnectionError as OpenAIAPIConnectionError,
    APIStatusError as OpenAIAPIStatusError,
    RateLimitError as OpenAIRateLimitError,
)
from ..config.loader import load_config

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
MAX_TRANSIENT_RETRIES = 6

_config = None
_anthropic_client = None
_local_client = None


def _get_config() -> dict:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def _get_anthropic() -> Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _anthropic_client = Anthropic(api_key=api_key, max_retries=8)
    return _anthropic_client


def _get_local() -> OpenAI:
    global _local_client
    if _local_client is None:
        cfg = _get_config()
        _local_client = OpenAI(
            base_url=cfg["backends"]["local"]["endpoint"],
            api_key=cfg["backends"]["local"]["api_key"],
            timeout=cfg["backends"]["local"]["request_timeout_seconds"],
        )
    return _local_client


@dataclass
class TokenTracker:
    claude_input: int = 0
    claude_output: int = 0
    local_input: int = 0
    local_output: int = 0
    calls_claude: int = 0
    calls_local: int = 0

    def add_claude(self, usage):
        self.claude_input += usage.input_tokens
        self.claude_output += usage.output_tokens
        self.calls_claude += 1

    def add_local(self, prompt_tokens: int, completion_tokens: int):
        self.local_input += prompt_tokens
        self.local_output += completion_tokens
        self.calls_local += 1

    def cost_estimate(self) -> float:
        # Sonnet 4 pricing (approximate); local is free
        return (self.claude_input / 1e6) * 3.0 + (self.claude_output / 1e6) * 15.0


TRACKER = TokenTracker()


def health_check_local() -> tuple[bool, str]:
    """Verify LM Studio is reachable and the configured model is loaded.
    Called at startup. Returns (ok, message)."""
    cfg = _get_config()
    endpoint = cfg["backends"]["local"]["endpoint"]
    model = cfg["backends"]["local"]["model"]
    try:
        r = requests.get(f"{endpoint}/models", timeout=5)
        r.raise_for_status()
        models = [m["id"] for m in r.json().get("data", [])]
        if model not in models and not any(model in m for m in models):
            return False, f"LM Studio is up but model '{model}' is not loaded. Loaded: {models}"
        return True, f"LM Studio OK at {endpoint}, model {model} loaded."
    except Exception as e:
        return False, f"LM Studio unreachable at {endpoint}: {e}"


def _strip_json_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)


def _retry_after_seconds(exc) -> float | None:
    """Read retry-after / retry-after-ms from an API error response, if any."""
    resp = getattr(exc, "response", None)
    if not resp:
        return None
    headers = getattr(resp, "headers", {}) or {}
    ra_ms = headers.get("retry-after-ms") or headers.get("Retry-After-Ms")
    if ra_ms is not None:
        try:
            return float(ra_ms) / 1000.0
        except (TypeError, ValueError):
            pass
    ra = headers.get("retry-after") or headers.get("Retry-After")
    if ra is not None:
        try:
            return float(ra)
        except (TypeError, ValueError):
            pass
    return None


def _sleep_for_transient(exc, retry: int, label: str, status: int | None = None):
    wait = _retry_after_seconds(exc)
    if wait is None:
        wait = min(2 ** retry, 60)
    descriptor = f"HTTP {status}" if status is not None else type(exc).__name__
    print(
        f"[llm_client] {label} {descriptor}; sleeping {wait:.1f}s "
        f"(retry {retry + 1}/{MAX_TRANSIENT_RETRIES})",
        file=sys.stderr,
    )
    time.sleep(wait)


def _call_claude_json(system: str, user: str, max_tokens: int) -> dict:
    cfg = _get_config()
    model = cfg["backends"]["claude"]["model"]
    client = _get_anthropic()
    last_exc: Exception | None = None
    for retry in range(MAX_TRANSIENT_RETRIES):
        try:
            for attempt in range(2):
                resp = client.messages.create(
                    model=model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": user}]
                )
                TRACKER.add_claude(resp.usage)
                text = resp.content[0].text
                try:
                    return json.loads(_strip_json_fences(text))
                except json.JSONDecodeError:
                    if attempt == 0:
                        user = user + "\n\nIMPORTANT: Respond with ONLY valid JSON. No preamble, no markdown fences."
                    else:
                        raise ValueError(f"Claude failed JSON parse after retry. Raw:\n{text}")
        except (AnthropicRateLimitError, AnthropicAPIConnectionError) as e:
            last_exc = e
            _sleep_for_transient(e, retry, "Claude")
            continue
        except AnthropicAPIStatusError as e:
            if e.status_code in RETRYABLE_STATUS:
                last_exc = e
                _sleep_for_transient(e, retry, "Claude", status=e.status_code)
                continue
            raise
    raise last_exc or RuntimeError("Claude call failed after rate-limit retries")


def _call_local_json(system: str, user: str, max_tokens: int) -> dict:
    cfg = _get_config()
    model = cfg["backends"]["local"]["model"]
    client = _get_local()
    last_exc: Exception | None = None
    for retry in range(MAX_TRANSIENT_RETRIES):
        try:
            for attempt in range(2):
                resp = client.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                )
                if hasattr(resp, "usage") and resp.usage:
                    TRACKER.add_local(resp.usage.prompt_tokens, resp.usage.completion_tokens)
                text = resp.choices[0].message.content
                try:
                    return json.loads(_strip_json_fences(text))
                except json.JSONDecodeError:
                    if attempt == 0:
                        user = user + "\n\nIMPORTANT: Respond with ONLY valid JSON. No preamble, no markdown fences."
                    else:
                        raise ValueError(f"Local model failed JSON parse after retry. Raw:\n{text}")
        except (OpenAIRateLimitError, OpenAIAPIConnectionError) as e:
            last_exc = e
            _sleep_for_transient(e, retry, "Local")
            continue
        except OpenAIAPIStatusError as e:
            if getattr(e, "status_code", None) in RETRYABLE_STATUS:
                last_exc = e
                _sleep_for_transient(e, retry, "Local", status=e.status_code)
                continue
            raise
    raise last_exc or RuntimeError("Local call failed after rate-limit retries")


def llm_call_json(task_name: str, system: str, user: str, max_tokens: int = 2000) -> dict:
    """Routes a JSON-output call to the configured backend for this task."""
    cfg = _get_config()
    backend = cfg["routing"].get(task_name, "claude")
    if backend == "local":
        return _call_local_json(system, user, max_tokens)
    else:
        return _call_claude_json(system, user, max_tokens)


def llm_call_json_batch(task_name: str, items: list[tuple[str, str, int]]) -> list[dict]:
    """Parallel JSON calls for batchable tasks (e.g., Librarian per-paper).
    items: list of (system, user, max_tokens) tuples.
    Returns results in input order. Errors become {'_error': str}.
    """
    cfg = _get_config()
    backend = cfg["routing"].get(task_name, "claude")
    parallel = cfg["backends"]["local"]["parallel_requests"] if backend == "local" else 2

    def _one(idx_and_item):
        idx, (sys_p, user_p, mt) = idx_and_item
        try:
            return idx, llm_call_json(task_name, sys_p, user_p, mt)
        except Exception as e:
            return idx, {"_error": str(e)}

    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        futures = [ex.submit(_one, (i, item)) for i, item in enumerate(items)]
        for fut in as_completed(futures):
            idx, result = fut.result()
            results[idx] = result
    return results