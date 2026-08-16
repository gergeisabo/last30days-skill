"""Shared Apify actor client for paid-per-event source modules.

Runs an actor synchronously (run-sync-get-dataset-items endpoint) with a hard
wall-clock timeout and returns at most ``item_cap`` items. All paid platforms
route through here so caps and timeouts live in exactly one place.

Env: APIFY_API_TOKEN (read by caller, passed in — never imported here).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

API_BASE = "https://api.apify.com/v2"
DEFAULT_TIMEOUT = 120  # seconds, run-sync overall budget
POLL_BACKOFF = 3.0  # base seconds between status polls (exponential-ish)


class ApifyError(RuntimeError):
    """Raised when an Apify actor run fails or returns an error payload."""


def run_sync(
    actor: str,
    run_input: dict,
    token: str,
    *,
    item_cap: int = 10,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict]:
    """Run an Apify actor synchronously and return up to ``item_cap`` dataset items.

    ``actor`` is the full name, e.g. ``"apify/facebook-posts-scraper"``.
    Uses the run-sync-get-dataset-items endpoint: one request that blocks until
    the run finishes (bounded by ``timeout``) and streams the dataset back.
    """
    if not token:
        raise ApifyError("missing APIFY_API_TOKEN")
    if item_cap <= 0:
        return []

    # API v2 wants actor IDs as username~name (tilde), not username/name.
    actor_id = urllib.parse.quote(actor.replace("/", "~"), safe="~")
    url = (
        f"{API_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
        f"?token={urllib.parse.quote(token)}&timeout={int(timeout)}&status=SUCCEEDED"
        f"&clean=true&format=json"
    )
    body = json.dumps(run_input).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    # http.post would work, but its retry loop on 5xx would double-bill on
    # actor-start events for genuinely failing runs, so a single raw request
    # with explicit error handling is safer for paid actors.
    try:
        with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise ApifyError(f"apify run failed: HTTP {exc.code} {detail}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ApifyError(f"apify run error: {exc}") from exc

    try:
        items = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApifyError(f"apify dataset not JSON: {payload[:200]!r}") from exc
    if isinstance(items, dict):
        # Some actors wrap results (e.g. {"items": [...]}) — unwrap.
        for key in ("items", "data", "results"):
            if isinstance(items.get(key), list):
                items = items[key]
                break
        else:
            items = [items] if items else []
    if not isinstance(items, list):
        items = [items] if items else []
    return items[:item_cap]
