"""
DocuSignSpokeWebhookUtil

Simulates a DocuSign envelope event delivery to the DocuSign Spoke external
trigger endpoint on a ServiceNow instance.  Acquires an OAuth 2.0 Bearer token
via the password grant flow and attaches it to the Spoke request.

Stdlib only — no third-party packages required.
Requires Python 3.8+.
"""

import base64
import http.cookiejar
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


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
# Result types                                                        #
# ------------------------------------------------------------------ #

@dataclass
class WebhookResult:
    success: bool
    status_code: int
    response_body: Optional[str]
    timestamp: str
    error_code: Optional[str]
    error_message: Optional[str]


@dataclass
class _TokenResult:
    success: bool
    access_token: Optional[str]
    status_code: int
    response_body: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]


# ------------------------------------------------------------------ #
# Main class                                                          #
# ------------------------------------------------------------------ #

class DocuSignSpokeWebhookUtil:

    TOKEN_PATH = '/oauth_token.do'
    SPOKE_PATH = (
        '/api/sn_docusign_spoke/docusign_esignature_external_trigger'
        '?X-SkipCookieAuthentication=true'
    )

    def __init__(
        self,
        instance_url: str,
        client_id: str,
        client_secret: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = True,
        debug: bool = False,
        use_basic_auth: bool = False,
    ):
        """
        :param instance_url:    Target SN instance base URL (trailing slash optional).
        :param client_id:       OAuth client ID (ignored when use_basic_auth=True).
        :param client_secret:   OAuth client secret (ignored when use_basic_auth=True).
        :param username:        SN username.
        :param password:        SN password.
        :param verify_ssl:      Set False to skip TLS certificate validation.
        :param debug:           Print verbose request/response details to stdout.
        :param use_basic_auth:  Skip OAuth; authenticate the Spoke request with HTTP
                                Basic auth (username:password) directly.  Useful when
                                OAuth client credentials are unavailable or the Bearer
                                token path does not trigger the flow.
        """
        self._instance_url   = (instance_url or '').rstrip('/')
        self._client_id      = client_id or ''
        self._client_secret  = client_secret or ''
        self._username       = username or ''
        self._password       = password or ''
        self._verify_ssl     = verify_ssl
        self._debug          = debug
        self._use_basic_auth = use_basic_auth
        # Shared across token and Spoke requests so SN session cookies
        # (glide_user_route, etc.) are automatically propagated.
        self._cookiejar      = http.cookiejar.CookieJar()

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
        if not self._username:
            missing.append('username')
        if not self._password:
            missing.append('password')
        if not self._use_basic_auth:
            if not self._client_id:
                missing.append('client_id')
            if not self._client_secret:
                missing.append('client_secret')
        if missing:
            return False, 'Missing required configuration: ' + ', '.join(missing)
        return True, ''

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
          - shared CookieJar so SN session cookies (glide_user_route, etc.) set by
            /oauth_token.do are automatically carried into the Spoke request,
            matching what RESTMessageV2 does when calling the same instance
        """
        return urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl_context()),
            urllib.request.HTTPCookieProcessor(self._cookiejar),
            _PostRedirectHandler(dbg=self._dbg if self._debug else None),
        )

    def _acquire_token(self) -> _TokenResult:
        """Acquire a Bearer token via OAuth 2.0 password grant from /oauth_token.do."""
        token_url = self._instance_url + self.TOKEN_PATH
        form_data = urllib.parse.urlencode({
            'grant_type':    'password',
            'client_id':     self._client_id,
            'client_secret': self._client_secret,
            'username':      self._username,
            'password':      self._password,
        }).encode('utf-8')

        req_headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept':       'application/json',
        }

        self._dbg('')
        self._dbg('── token request ─────────────────────────────────────────────')
        self._dbg(f'  POST {token_url}')
        self._dbg('  headers:')
        self._dbg_headers(req_headers)
        self._dbg(
            f'  body: grant_type=password'
            f'&client_id=***&client_secret=***'
            f'&username={self._username}&password=***'
        )

        req = urllib.request.Request(
            token_url, data=form_data, headers=req_headers, method='POST',
        )
        status_code, body = -1, None

        try:
            with self._opener().open(req) as resp:
                status_code = resp.status
                body        = resp.read().decode('utf-8')
                self._dbg('')
                self._dbg('── token response ────────────────────────────────────────────')
                self._dbg(f'  status: {status_code}')
                self._dbg('  headers:')
                self._dbg_response_headers(resp)
                self._dbg(f'  cookies set: {[c.name for c in self._cookiejar]}')
                token_preview = body[:80] + '...' if len(body) > 80 else body
                self._dbg(f'  body: {token_preview}')
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            try:
                body = exc.read().decode('utf-8')
            except Exception:
                pass
            self._dbg(f'── token response (HTTP error) status={status_code} body={body}')
            return _TokenResult(
                False, None, status_code, body,
                'TOKEN_RESPONSE_ERROR',
                f'OAuth token request returned HTTP {status_code}. '
                f'Verify instance_url, client_id, and client_secret. Response: {body}',
            )
        except urllib.error.URLError as exc:
            return _TokenResult(
                False, None, -1, None,
                'TOKEN_REQUEST_ERROR',
                f'Network or transport error during token request to {token_url}: {exc.reason}',
            )
        except Exception as exc:
            return _TokenResult(
                False, None, -1, None,
                'TOKEN_REQUEST_ERROR',
                f'Unexpected error during token request to {token_url}: {exc}',
            )

        if status_code != 200:
            return _TokenResult(
                False, None, status_code, body,
                'TOKEN_RESPONSE_ERROR',
                f'OAuth token request returned HTTP {status_code}. '
                f'Verify instance_url, client_id, and client_secret. Response: {body}',
            )

        try:
            token_data: Dict[str, Any] = json.loads(body)
        except Exception as exc:
            return _TokenResult(
                False, None, status_code, body,
                'TOKEN_PARSE_ERROR',
                f'Token response is not valid JSON: {exc}. Raw body: {body}',
            )

        access_token = token_data.get('access_token')
        if not access_token:
            return _TokenResult(
                False, None, status_code, body,
                'TOKEN_PARSE_ERROR',
                f'Token response did not contain an access_token field. Response: {body}',
            )

        return _TokenResult(True, access_token, status_code, body, None, None)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def forward_event(
        self,
        body: str,
        additional_headers: Optional[Dict[str, str]] = None,
    ) -> WebhookResult:
        """
        Forward a DocuSign envelope event to the Spoke external trigger endpoint.

        Auth modes (controlled by use_basic_auth constructor flag):
          Bearer (default) – acquire OAuth token from /oauth_token.do, attach as
                             Authorization: Bearer <token>.
          Basic            – encode username:password as Authorization: Basic <b64>.
                             Skips the OAuth step entirely; useful if the Bearer
                             path is not triggering the flow.

        Error codes on failure:
          INVALID_CONFIG      – required fields missing.
          INVALID_PAYLOAD     – body is empty or not a string.
          TOKEN_REQUEST_ERROR – network failure reaching /oauth_token.do.
          TOKEN_RESPONSE_ERROR– non-200 from /oauth_token.do.
          TOKEN_PARSE_ERROR   – token response missing access_token.
          SPOKE_REQUEST_ERROR – network failure reaching the Spoke endpoint.

        Non-2xx HTTP from the Spoke endpoint → success=True with the real status code.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        valid, err_msg = self._validate_config()
        if not valid:
            return WebhookResult(False, -1, None, timestamp, 'INVALID_CONFIG', err_msg)

        if not body or not isinstance(body, str):
            return WebhookResult(
                False, -1, None, timestamp,
                'INVALID_PAYLOAD', 'body must be a non-empty JSON string',
            )

        # ── Resolve Authorization header ────────────────────────────── #
        if self._use_basic_auth:
            cred        = base64.b64encode(
                f'{self._username}:{self._password}'.encode('utf-8')
            ).decode('ascii')
            auth_header = f'Basic {cred}'
            self._dbg('')
            self._dbg('── auth mode: Basic (skipping OAuth token step) ──────────────')
        else:
            token_result = self._acquire_token()
            if not token_result.success:
                return WebhookResult(
                    False,
                    token_result.status_code,
                    token_result.response_body,
                    timestamp,
                    token_result.error_code,
                    token_result.error_message,
                )
            auth_header = f'Bearer {token_result.access_token}'

        # ── Spoke request ────────────────────────────────────────────── #
        url = self._instance_url + self.SPOKE_PATH
        req_headers = {
            'Content-Type':  'application/json',
            'Accept':        'application/json',
            'Authorization': auth_header,
        }
        if additional_headers:
            req_headers.update(additional_headers)

        self._dbg('')
        self._dbg('── spoke request ─────────────────────────────────────────────')
        self._dbg(f'  POST {url}')
        self._dbg('  headers:')
        self._dbg_headers(req_headers, redact=['Authorization'])
        self._dbg(f'  cookies in jar: {[c.name for c in self._cookiejar]}')
        self._dbg(f'  body ({len(body)} chars): {body[:120]}{"..." if len(body) > 120 else ""}')

        req = urllib.request.Request(
            url, data=body.encode('utf-8'), headers=req_headers, method='POST',
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
