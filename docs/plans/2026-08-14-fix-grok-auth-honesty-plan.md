# Plan: Fix Grok Auth Honesty

**Date:** 2026-08-14  
**Status:** Implemented  
**PR:** fix(grok): treat expired sessions as error, skip at research time

## Problem (measured 2026-08-14 Peter Steinberger run on the user's Mac)

- grok binary on PATH. Doctor cached grok status ok / will use grok because `~/.grok/auth.json` existed with token markers.
- `stored_auth_status()` substring-scans for `refresh_token`/`access_token`/`auth_mode`. It never parses `expires_at`.
- The file had `expires_at 2026-08-14T01:26:53Z`, hours dead.
- A prior run at 07:47:26 UTC had `run_outcome` ok (2 items). Session was real.
- At 08:43 grok loaded auth, `is_expired` true, OIDC refresh → `invalid_grant` "Refresh token has been revoked". grok deleted auth.json.
- Engine exit 1 "Not signed in", fell back to bird (30 items via Safari cookies), lane flagged PARTIAL.
- Host told the user "Grok CLI is not signed in" as if it never was.

## Three states to distinguish

1. **No grok CLI** — silent fallback. Fine. Do not waste the user's time. Do not nag install on every research run.
2. **CLI installed, never logged in** — silent fallback. Fine.
3. **CLI installed, WAS logged in, session dead** — currently reports ok then partial. **This is the bug.**

## What was built

1. **`stored_auth_status` parses `expires_at` locally** (no network, no subprocess). Added `AUTH_EXPIRED` distinct from `AUTH_OK` / `AUTH_MISSING` / `AUTH_ERROR`. Never echoes token values. Finds `expires_at` anywhere in the vendor-keyed JSON object via recursive search. **Unparseable JSON or unparseable `expires_at` with credential markers → fail closed (AUTH_EXPIRED)** — do not claim OK when the store cannot be verified.

2. **Doctor / `_probe_grok` maps `AUTH_EXPIRED` to `health.ERROR`** (configured-but-broken), not DEGRADED. This ensures collect-then-pick will NOT select grok; a fallback backend (bird) is selected instead. Reports expiry timestamp + "skipped to avoid credential-store wipe on failed refresh — run `grok login --device-auth`".

3. **Research-time `has_stored_auth` / `is_available` return False when expired.** We skip expired sessions because invoking grok when the local token is past `expires_at` triggers OIDC refresh — and when that refresh fails (revoked session), grok DELETES `auth.json`. A 15–45 s call that wipes the credential store is worse than skipping. **last30days does not IdP-refresh a locally expired access token.**

4. **Auth revocation detection at run time**: If grok exits "Not signed in" / RefreshTokenRejected / auth.json vanished mid-run: does not retry grok in that run. Falls back once. Typed outcome `auth-failed` (via `is_auth_revoked_error()` and `classify_run_failure()`), not a generic PARTIAL.

5. **Fallback-fully-served is OK, not PARTIAL or AUTH_FAILED**: When a fallback backend (bird) fully serves the request after the primary (grok) auth-failed, the source status is now `ok` with informational detail ("X served via bird after grok: grok session expired"). PARTIAL is for incomplete retrieval, not successful fallback.

6. **http.classify_failure auth markers**: Added "not signed in", "invalid_grant", "refresh token", "session expired", "grok session expired" to map to AUTH_FAILED.

7. **Doctor cache fingerprint**: Extended to include grok auth signal (auth_state + expires_at ISO or "absent") so `--cached` doesn't keep "will use: grok" after grok deleted the file.

8. **`_x_record` path verified**: With `has_stored_auth()` returning False when expired, the `_x_record` path cannot resurrect expired grok as ok.

9. **bird_authenticated diagnosis**: If `bird_authenticated` is False but `x_pending_browser_auth` is True, the diagnosis now reflects that bird WILL be authenticated via browser cookies at run time.

10. **New prescription**: `("x", "grok_session_expired")` for hosts to use when grok expired but fallback succeeded.

11. **Host-facing copy for case 3**: SKILL.md updated with guidance: "Grok session expired at {timestamp}; X was served via browser cookies. Re-run `grok login --device-auth` when online to restore the grok path" — not "Grok CLI is not signed in" which misrepresents the history. Do not nag about installing grok when it's absent.

12. **Doctor --probe still does not call xAI or grok.** Whole-doctor-path test patches `subprocess.run` to raise and still passes.

13. **Tests**: Extended `test_grok_x.py` (expires_at parsing, fail-closed on unparseable), `test_backend_descriptors.py` (grok ERROR not DEGRADED), `test_doctor_cache.py` (grok state change invalidates), `test_pipeline_v3.py` (fallback-served is OK).

## Scope boundaries (NOT in this PR)

- X query construction, fanout, `search_name`, retrieve-judge-retry, and handle promotion are unchanged. That is a separate PR.

## Success criteria (all met)

- Past `expires_at` fixture → not grok ok, has_stored_auth False, is_available False.
- Future `expires_at` → still ok (not live-verified).
- Unparseable JSON / expires_at with markers → AUTH_EXPIRED (fail closed).
- No grok binary → no extra user-facing failure, no install nag.
- Expired grok with bird fallback → bird selected, source status OK.
- Fallback-fully-served → state OK, not PARTIAL or AUTH_FAILED.
- Grok state change invalidates doctor cache.
- No-subprocess doctor test still passes.

## Files changed

- `skills/last30days/scripts/lib/grok_x.py` — `AUTH_EXPIRED`, `stored_auth_status()` returns 3-tuple with fail-closed on unparseable, `has_stored_auth()`/`is_available()` return False when expired, `is_auth_revoked_error()`, `classify_run_failure()`, `_invoke()` sets `auth_revoked`, `_run_query()` returns 3-tuple, `search_x()` propagates `auth_revoked`
- `skills/last30days/scripts/lib/backends.py` — `_probe_grok()` handles `AUTH_EXPIRED` as `health.ERROR`
- `skills/last30days/scripts/lib/http.py` — `classify_failure()` auth markers for grok
- `skills/last30days/scripts/lib/pipeline.py` — `_fetch_x_backend()` propagates `auth_revoked`, fallback-served → OK, `_classify_source_failure()` recognizes grok markers, `_finalize_source_status()` handles AUTH_FAILED with items, `diagnose()` bird_authenticated reflects x_pending_browser_auth
- `skills/last30days/scripts/lib/doctor.py` — `_grok_cache_signal()` for fingerprint, `_config_fingerprint()` includes grok
- `skills/last30days/scripts/lib/prescriptions.py` — `("x", "grok_session_expired")` entry
- `skills/last30days/SKILL.md` — Grok session expiry handling guidance updated
- `tests/test_grok_x.py` — expires_at, fail-closed, has_stored_auth/is_available False when expired
- `tests/test_backend_descriptors.py` — grok ERROR not DEGRADED
- `tests/test_doctor_cache.py` — grok state change invalidates cache
- `tests/test_pipeline_v3.py` — fallback-served is OK
- `changelog.d/+grok-auth-expired.fixed.md` — release notes fragment
