"""
GitHubSpokeWebhookUtil

Simulates a GitHub webhook delivery to the GitHub Spoke external trigger
endpoint on a ServiceNow instance.  Computes an HMAC-SHA256 signature over
the payload using the shared secret and attaches the required GitHub headers.

Stdlib only — no third-party packages required.
Requires Python 3.8+.
"""

import hashlib
import hmac
import http.cookiejar
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple


# ------------------------------------------------------------------ #
# Redirect handler                                                    #
# ------------------------------------------------------------------ #

class _PostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Preserve the POST method and body on 301/302 redirects.

    urllib's default handler (handler_order=500) converts POST→GET on 301/302.
    Setting handler_order=499 ensures this subclass is called first so the
    default handler never gets a chance to strip the body.
    """

    handler_order = 499

    def __init__(self, dbg=None):
        super().__init__()
        self._dbg = dbg

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if self._dbg:
            self._dbg(f'redirect {code} → {newurl}  (preserving POST body)')
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None and req.method == 'POST':
            new_req.method = 'POST'
            new_req.data   = req.data
        return new_req


# ------------------------------------------------------------------ #
# Result type                                                         #
# ------------------------------------------------------------------ #

@dataclass
class WebhookResult:
    success: bool
    status_code: int
    response_body: Optional[str]
    timestamp: str
    error_code: Optional[str]
    error_message: Optional[str]


# ------------------------------------------------------------------ #
# Main class                                                          #
# ------------------------------------------------------------------ #

class GitHubSpokeWebhookUtil:

    SPOKE_PATH = (
        '/api/sn_github_spoke/github_external_trigger'
        '?X-SkipCookieAuthentication=true'
    )

    def __init__(
        self,
        instance_url: str,
        shared_secret: str,
        *,
        verify_ssl: bool = True,
        debug: bool = False,
    ):
        """
        :param instance_url:   Target SN instance base URL (trailing slash optional).
        :param shared_secret:  GitHub webhook shared secret used for HMAC signing.
        :param verify_ssl:     Set False to skip TLS certificate validation (lab instances).
        :param debug:          Print verbose request/response details to stdout.
        """
        self._instance_url  = (instance_url or '').rstrip('/')
        self._shared_secret = shared_secret or ''
        self._verify_ssl    = verify_ssl
        self._debug         = debug
        self._cookiejar     = http.cookiejar.CookieJar()

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _dbg(self, msg: str) -> None:
        if self._debug:
            print(f'[DBG] {msg}')

    def _dbg_headers(self, headers: Dict[str, str], redact: Optional[List[str]] = None) -> None:
        if not self._debug:
            return
        for k, v in headers.items():
            if redact and k.lower() in [r.lower() for r in redact]:
                v = v[:20] + '...[redacted]' if len(v) > 20 else '***'
            self._dbg(f'    {k}: {v}')

    def _dbg_response_headers(self, resp) -> None:
        if not self._debug:
            return
        for k, v in resp.headers.items():
            self._dbg(f'    {k}: {v}')

    def _validate_config(self) -> Tuple[bool, str]:
        missing = []
        if not self._instance_url:
            missing.append('instance_url')
        if not self._shared_secret:
            missing.append('shared_secret')
        if missing:
            return False, 'Missing required configuration: ' + ', '.join(missing)
        return True, ''

    def _compute_signature(self, payload: str) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
        """Returns (success, hex_digest, error_code, error_message)."""
        try:
            digest = hmac.new(
                key=self._shared_secret.encode('utf-8'),
                msg=payload.encode('utf-8'),
                digestmod=hashlib.sha256,
            ).hexdigest()
            return True, digest, None, None
        except Exception as exc:
            return False, None, 'SIGNATURE_ERROR', f'Failed to compute HMAC-SHA256 signature: {exc}'

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not self._verify_ssl:
            ctx.check_hostname = False
            ctx.verify_mode    = ssl.CERT_NONE
        return ctx

    def _opener(self) -> urllib.request.OpenerDirector:
        """
        Opener with:
          - custom SSL context (supports --no-verify-ssl)
          - POST-preserving redirect handler (runs before the default order-500 handler)
          - CookieJar so SN session cookies (glide_user_route etc.) are carried
            between requests, matching RESTMessageV2 behaviour
        """
        return urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl_context()),
            urllib.request.HTTPCookieProcessor(self._cookiejar),
            _PostRedirectHandler(dbg=self._dbg if self._debug else None),
        )

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def forward_event(
        self,
        payload: str,
        event_type: str,
        hook_id: str = '',
        additional_headers: Optional[Dict[str, str]] = None,
    ) -> WebhookResult:
        """
        Forward a GitHub webhook event to the Spoke external trigger endpoint.

        Error codes on failure:
          INVALID_CONFIG      – instance_url or shared_secret missing.
          INVALID_PAYLOAD     – payload is empty or not a string.
          INVALID_EVENT_TYPE  – event_type is empty.
          SIGNATURE_ERROR     – HMAC computation raised an exception.
          SPOKE_REQUEST_ERROR – Network / transport failure.

        Non-2xx HTTP from the Spoke endpoint → success=True with the real status code.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        valid, err_msg = self._validate_config()
        if not valid:
            return WebhookResult(False, -1, None, timestamp, 'INVALID_CONFIG', err_msg)

        if not payload or not isinstance(payload, str):
            return WebhookResult(
                False, -1, None, timestamp,
                'INVALID_PAYLOAD', 'payload must be a non-empty JSON string',
            )

        if not event_type:
            return WebhookResult(
                False, -1, None, timestamp,
                'INVALID_EVENT_TYPE',
                'event_type is required (e.g. "release", "push", "pull_request")',
            )

        ok, sig, err_code, err_msg = self._compute_signature(payload)
        if not ok:
            return WebhookResult(False, -1, None, timestamp, err_code, err_msg)

        url = self._instance_url + self.SPOKE_PATH
        headers = {
            'Content-Type':        'application/json',
            'Accept':              'application/json',
            'x-hub-signature-256': f'sha256={sig}',
            'x-github-event':      event_type,
            'x-github-hook-id':    hook_id or '',
        }
        if additional_headers:
            headers.update(additional_headers)

        self._dbg('')
        self._dbg('── spoke request ─────────────────────────────────────────────')
        self._dbg(f'  POST {url}')
        self._dbg('  headers:')
        self._dbg_headers(headers, redact=['x-hub-signature-256'])
        self._dbg(f'  body ({len(payload)} chars): {payload[:120]}{"..." if len(payload) > 120 else ""}')

        req = urllib.request.Request(
            url, data=payload.encode('utf-8'), headers=headers, method='POST',
        )
        status_code, response_body = -1, None

        try:
            with self._opener().open(req) as resp:
                status_code   = resp.status
                response_body = resp.read().decode('utf-8')
                self._dbg('')
                self._dbg('── spoke response ────────────────────────────────────────────')
                self._dbg(f'  status: {status_code}')
                self._dbg('  headers:')
                self._dbg_response_headers(resp)
                self._dbg(f'  body: {response_body[:300]}{"..." if len(response_body) > 300 else ""}')
                self._dbg(f'  cookies set: {[c.name + "=" + c.value[:10] + "..." for c in self._cookiejar]}')
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            try:
                response_body = exc.read().decode('utf-8')
            except Exception:
                pass
            self._dbg(f'── spoke response (HTTP error) status={status_code} body={response_body}')
        except urllib.error.URLError as exc:
            return WebhookResult(
                False, -1, None, timestamp, 'SPOKE_REQUEST_ERROR',
                f'Network or transport error during Spoke call to {url}: {exc.reason}',
            )
        except Exception as exc:
            return WebhookResult(
                False, -1, None, timestamp, 'SPOKE_REQUEST_ERROR',
                f'Unexpected error during Spoke call to {url}: {exc}',
            )

        return WebhookResult(True, status_code, response_body, timestamp, None, None)
