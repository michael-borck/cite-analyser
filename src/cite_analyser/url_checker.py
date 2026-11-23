"""URL liveness — port of urlChecker.ts.

Only 404/410 mean "dead"; 401/403/429/451 and 5xx mean the server declined an
automated client (paywall, anti-bot, auth) — reachable but unverifiable, so
academic publishers' 403s never become false "broken link" positives.
"""

from __future__ import annotations

import urllib.parse

import httpx

from .providers import _throttle, is_private_url

TIMEOUT = 10.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; cite-analyser/0.1; +https://github.com/michael-borck/cite-analyser)"
)
ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def check_url(url: str) -> dict:
    """Check whether a URL is accessible.

    Returns {url, status, statusCode?, finalUrl?, error?} where status is one
    of live | redirect | dead | blocked | timeout | error.
    """
    if is_private_url(url):
        return {
            "url": url,
            "status": "error",
            "error": "URL points to a private or internal network address",
        }

    try:
        service = urllib.parse.urlparse(url).hostname or "web"
    except ValueError:
        service = "web"

    try:
        _throttle(service)
        res = httpx.get(
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(TIMEOUT),
            headers={"User-Agent": USER_AGENT, "Accept": ACCEPT},
        )
    except httpx.TimeoutException:
        return {"url": url, "status": "timeout", "error": "Request timed out after 10 seconds"}
    except httpx.HTTPError as e:
        return {"url": url, "status": "error", "error": str(e)}

    status_code = res.status_code
    final_url = str(res.url) if str(res.url) != url else None

    if 200 <= status_code < 300:
        status = "live"
    elif 300 <= status_code < 400:
        status = "redirect"
    elif status_code in (404, 410):
        status = "dead"
    else:
        status = "blocked"

    result = {"url": url, "status": status, "statusCode": status_code}
    if final_url:
        result["finalUrl"] = final_url
    return result
