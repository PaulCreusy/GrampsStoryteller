"""
Minimal JSON over HTTP client based on the standard library.

Gramps runs plugins inside its own Python interpreter, so the addon must not
depend on third-party packages such as requests.
"""

###########
# Imports #
###########

# Python imports #
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Optional

# Local imports #
from . import __version__

#############
# Constants #
#############

USER_AGENT = (
    "GrampsStoryteller/%s (https://github.com/PaulCreusy/GrampsStoryteller)"
    % __version__
)
RETRY_STATUSES = (429, 500, 502, 503, 504, 529)
MAX_RETRY_AFTER = 60

###########
# Classes #
###########


class HttpError(Exception):
    """
    HTTP or network failure.

    Parameters
    ----------
    message : str
        Human readable message.
    status : int or None
        HTTP status code, None for network errors.
    body : str
        Raw response body, if any.
    """

    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


class Cancelled(Exception):
    """Raised when the user stopped the processing."""


#############
# Functions #
#############


def build_url(base: str, params: Optional[Dict[str, object]] = None) -> str:
    """
    Append encoded query parameters to a URL.

    Parameters
    ----------
    base : str
        URL without query string.
    params : dict, optional
        Query parameters.

    Returns
    -------
    str
        Full URL.
    """
    if not params:
        return base
    return base + "?" + urllib.parse.urlencode(params)


def _error_message(status: int, body: str) -> str:
    """
    Extract a readable error message from an API error body.

    Parameters
    ----------
    status : int
        HTTP status code.
    body : str
        Raw body.

    Returns
    -------
    str
        Message.
    """
    detail = body.strip()
    try:
        data = json.loads(body)
        error = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(error, dict):
            detail = error.get("message") or json.dumps(error)
        elif isinstance(error, str):
            detail = error
    except ValueError:
        pass
    return "HTTP %d: %s" % (status, detail[:500])


def request_json(
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[object] = None,
    timeout: float = 120,
    retries: int = 3,
    should_stop: Optional[Callable[[], bool]] = None,
) -> object:
    """
    Send a request and decode the JSON response, retrying transient errors.

    Parameters
    ----------
    url : str
        Target URL.
    method : str
        HTTP method.
    headers : dict, optional
        Extra headers.
    payload : object, optional
        JSON-serializable body.
    timeout : float
        Timeout of one attempt, in seconds.
    retries : int
        Number of retries after the first attempt for 429/5xx and network
        errors.
    should_stop : callable, optional
        Returns True when the user cancelled; checked between attempts.

    Returns
    -------
    object
        Decoded JSON.

    Raises
    ------
    HttpError
        On a non-retryable error or when retries are exhausted.
    Cancelled
        When should_stop returned True.
    """
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        all_headers["Content-Type"] = "application/json"
    all_headers.update(headers or {})

    attempt = 0
    while True:
        if should_stop and should_stop():
            raise Cancelled()
        request = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        delay = 2 ** (attempt + 1)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
            try:
                return json.loads(raw)
            except ValueError as exc:
                raise HttpError("Invalid JSON response from %s" % url, body=raw[:500]) from exc
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code not in RETRY_STATUSES or attempt >= retries:
                raise HttpError(_error_message(exc.code, body), exc.code, body) from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if retry_after and retry_after.isdigit():
                delay = min(int(retry_after), MAX_RETRY_AFTER)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt >= retries:
                reason = getattr(exc, "reason", exc)
                raise HttpError("Network error: %s" % reason) from exc
        attempt += 1
        _sleep(delay, should_stop)


def _sleep(seconds: float, should_stop: Optional[Callable[[], bool]]):
    """
    Sleep while regularly checking for cancellation.

    Parameters
    ----------
    seconds : float
        Duration.
    should_stop : callable, optional
        Cancellation check.

    Raises
    ------
    Cancelled
        When should_stop returned True.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if should_stop and should_stop():
            raise Cancelled()
        time.sleep(min(0.2, max(0.0, end - time.monotonic())))
