# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "playwright",
# ]
# ///
"""
Interactive Playwright investigation of services.spservices.sg.

Run this LOCALLY (not on a remote server) with:

    uv run investigate_spservices.py

What it does:
  1. Opens a real Chrome window at the SP Services login page.
  2. Records every XHR/Fetch API call you trigger.
  3. You log in manually, complete the OTP, and navigate to the usage page.
  4. Press ENTER in the terminal when done.
  5. Saves a summary + full log to:
       spservices_findings.json   (structured)
       spservices_network_log.json (raw, with headers)
       spservices_screenshot.png  (final page screenshot)

Share spservices_findings.json so the coordinator endpoints can be updated.
"""

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import Page, async_playwright

_BASE_URL = "https://services.spservices.sg"
_OUT_DIR = Path(__file__).parent

# Regex to extract REST API paths from bundled JS
_API_PATH_RE = re.compile(
    r"""["'`](/(?:api|v\d|rest|service|services|account|usage|auth|login|otp)[^"'`\s]{0,120})["'`]"""
)
_CONFIG_KEY_RE = re.compile(
    r"""(?:baseUrl|apiUrl|apiBase|endpointUrl|serviceUrl|BASE_URL|API_URL)\s*[:=]\s*["'`]([^"'`\s]+)["'`]""",
    re.IGNORECASE,
)


def _redact(obj: object) -> object:
    """Redact passwords and tokens from dicts (for safe logging)."""
    if not isinstance(obj, dict):
        return obj
    safe: dict = {}
    for k, v in obj.items():
        kl = k.lower()
        if any(w in kl for w in ("pass", "secret", "token", "auth", "key")):
            safe[k] = "***REDACTED***"
        else:
            safe[k] = _redact(v)
    return safe


async def _scan_js(page: Page, src: str) -> dict:
    """Fetch one JS bundle URL and grep it for API paths / config keys."""
    result: dict = {"url": src, "api_paths": [], "config_keys": []}
    try:
        resp = await page.request.get(src, timeout=15_000)
        if not resp.ok:
            return result
        text = await resp.text()
        result["api_paths"] = sorted(set(_API_PATH_RE.findall(text)))
        result["config_keys"] = [
            {"match": m.group(0)[:100], "value": m.group(1)}
            for m in _CONFIG_KEY_RE.finditer(text)
        ]
    except Exception as exc:
        result["error"] = str(exc)
    return result


async def _probe_configs(page: Page) -> dict:
    """Try well-known Angular/SPA config JSON paths."""
    found: dict = {}
    for path in (
        "/assets/env.json",
        "/assets/config.json",
        "/assets/app-config.json",
        "/assets/settings.json",
        "/environment.json",
        "/config.json",
    ):
        try:
            resp = await page.request.get(
                f"{_BASE_URL}{path}", timeout=5_000
            )
            if resp.ok:
                try:
                    found[path] = await resp.json()
                except Exception:
                    found[path] = (await resp.text())[:500]
        except Exception:
            pass
    return found


async def main() -> None:  # noqa: C901
    requests_log: list[dict] = []
    findings: dict = {
        "login_page": {},
        "otp_page": {},
        "usage_page": {},
        "api_calls": [],
        "js_api_findings": [],
        "config_endpoints": {},
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=30)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()

        # ------------------------------------------------------------
        # Intercept all requests (live print + log)
        # ------------------------------------------------------------
        async def on_request(request) -> None:
            if request.resource_type not in ("fetch", "xhr"):
                return
            entry: dict = {
                "ts": datetime.now().isoformat(timespec="milliseconds"),
                "method": request.method,
                "url": request.url,
                "request_headers": dict(request.headers),
                "post_data": None,
                "response_status": None,
                "response_body": None,
            }
            try:
                pd = request.post_data
                if pd:
                    try:
                        entry["post_data"] = json.loads(pd)
                    except Exception:
                        entry["post_data"] = pd
            except Exception:
                pass
            requests_log.append(entry)
            print(
                f"  → {request.method:<6} {request.url}",
                flush=True,
            )
            if entry["post_data"]:
                print(
                    f"       BODY: {json.dumps(_redact(entry['post_data']))}",
                    flush=True,
                )

        async def on_response(response) -> None:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            try:
                body = await response.json()
            except Exception:
                return
            for entry in reversed(requests_log):
                if entry["url"] == response.url:
                    entry["response_status"] = response.status
                    entry["response_body"] = body
                    break
            safe_body = _redact(body) if isinstance(body, dict) else body
            print(
                f"  ← {response.status:<4} {response.url}",
                flush=True,
            )
            if isinstance(safe_body, dict) and len(str(safe_body)) < 400:
                print(f"       RESP: {json.dumps(safe_body)}", flush=True)

        page.on("request", on_request)
        page.on("response", on_response)

        # ------------------------------------------------------------
        # Navigate to login page
        # ------------------------------------------------------------
        print(f"\nOpening {_BASE_URL}/#/login …\n")
        await page.goto(f"{_BASE_URL}/#/login", wait_until="networkidle")

        # Capture login page structure
        findings["login_page"] = {
            "url": page.url,
            "title": await page.title(),
            "inputs": [
                {
                    "name": await el.get_attribute("name"),
                    "id": await el.get_attribute("id"),
                    "type": await el.get_attribute("type"),
                    "placeholder": await el.get_attribute("placeholder"),
                    "formControlName": (
                        await el.get_attribute("formcontrolname")
                        or await el.get_attribute("ng-model")
                    ),
                    "autocomplete": await el.get_attribute("autocomplete"),
                }
                for el in await page.query_selector_all("input")
            ],
            "buttons": [
                {
                    "text": (await el.inner_text()).strip(),
                    "type": await el.get_attribute("type"),
                }
                for el in await page.query_selector_all("button")
            ],
        }
        print(f"Login page title: {findings['login_page']['title']}")
        print(f"Inputs found: {findings['login_page']['inputs']}")

        # Probe config endpoints while waiting
        print("\nProbing static config endpoints …")
        findings["config_endpoints"] = await _probe_configs(page)
        for path, val in findings["config_endpoints"].items():
            print(f"  FOUND {path}")

        # Scan JS bundles for API paths
        script_srcs = [
            (await el.get_attribute("src") or "")
            for el in await page.query_selector_all("script[src]")
        ]
        script_srcs = [
            s if s.startswith("http") else f"{_BASE_URL}{s}"
            for s in script_srcs
            if s
        ]
        print(f"\nScanning {min(len(script_srcs), 8)} JS bundles for API paths …")
        for src in script_srcs[:8]:
            result = await _scan_js(page, src)
            if result.get("api_paths") or result.get("config_keys"):
                findings["js_api_findings"].append(result)
                for path in result["api_paths"][:20]:
                    print(f"  API path: {path}")
                for cfg in result.get("config_keys", []):
                    print(f"  Config:   {cfg['match']!r} → {cfg['value']!r}")

        # ------------------------------------------------------------
        # Wait for user to log in manually
        # ------------------------------------------------------------
        print("\n" + "=" * 62)
        print("BROWSER IS OPEN — complete the login + OTP flow manually.")
        print("Navigate to the energy/water USAGE page when done.")
        print("=" * 62)
        print("\nCapturing API calls in real-time (see above) …")
        print("Press ENTER here when you are on the usage page.\n")
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, input, "> ")
        except (KeyboardInterrupt, EOFError):
            pass

        # Capture state after login
        findings["usage_page"] = {
            "url": page.url,
            "title": await page.title(),
            "inputs": [
                {
                    "name": await el.get_attribute("name"),
                    "id": await el.get_attribute("id"),
                    "type": await el.get_attribute("type"),
                }
                for el in await page.query_selector_all("input")
            ],
        }

        # Screenshot
        ss_path = _OUT_DIR / "spservices_screenshot.png"
        await page.screenshot(path=str(ss_path), full_page=True)
        print(f"Screenshot saved: {ss_path}")

        await browser.close()

    # ------------------------------------------------------------
    # Build findings summary (API calls only, no static assets)
    # ------------------------------------------------------------
    api_calls = [
        e
        for e in requests_log
        if not any(
            e["url"].endswith(ext)
            for ext in (".js", ".css", ".png", ".ico", ".woff", ".woff2", ".map", ".svg")
        )
    ]
    findings["api_calls"] = [
        {
            "method": e["method"],
            "url": e["url"],
            "post_data": _redact(e["post_data"]) if e.get("post_data") else None,
            "response_status": e.get("response_status"),
            "response_body": (
                _redact(e["response_body"])
                if isinstance(e.get("response_body"), dict)
                else e.get("response_body")
            ),
        }
        for e in api_calls
    ]

    # Save structured findings
    findings_path = _OUT_DIR / "spservices_findings.json"
    with open(findings_path, "w") as f:
        json.dump(findings, f, indent=2, default=str)
    print(f"Findings saved:  {findings_path}")

    # Save raw log (full headers)
    raw_path = _OUT_DIR / "spservices_network_log.json"
    with open(raw_path, "w") as f:
        safe_log = [
            {
                **e,
                "post_data": _redact(e["post_data"]) if isinstance(e.get("post_data"), dict) else e.get("post_data"),
                "response_body": _redact(e["response_body"]) if isinstance(e.get("response_body"), dict) else e.get("response_body"),
            }
            for e in requests_log
        ]
        json.dump(safe_log, f, indent=2, default=str)
    print(f"Raw log saved:   {raw_path}")

    # ------------------------------------------------------------
    # Human-readable summary
    # ------------------------------------------------------------
    print("\n" + "=" * 62)
    print("API CALL SUMMARY (what to look for in coordinator.py)")
    print("=" * 62)
    for call in findings["api_calls"]:
        print(f"\n{call['method']} {call['url']}")
        if call.get("post_data"):
            print(f"  BODY:   {json.dumps(call['post_data'])}")
        if call.get("response_status"):
            print(f"  STATUS: {call['response_status']}")
        rb = call.get("response_body")
        if isinstance(rb, dict) and len(str(rb)) < 400:
            print(f"  RESP:   {json.dumps(rb)}")

    all_paths: set[str] = set()
    for chunk in findings.get("js_api_findings", []):
        all_paths.update(chunk.get("api_paths", []))
    if all_paths:
        print(f"\nAPI paths extracted from JS bundles:")
        for p in sorted(all_paths):
            print(f"  {p}")

    print(f"\nShare {findings_path.name} to update sp_services_coordinator.py")


if __name__ == "__main__":
    asyncio.run(main())
