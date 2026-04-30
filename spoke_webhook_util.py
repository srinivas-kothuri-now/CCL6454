"""
Spoke Webhook Simulator  (CCL6454)

CLI entry point that dispatches to GitHubSpokeWebhookUtil or
DocuSignSpokeWebhookUtil.  Run with no arguments for guided interactive mode;
supply a subcommand for scripted / headless use.

Usage — interactive (workshop default):
    python spoke_webhook_util.py

Usage — CLI:
    python spoke_webhook_util.py github \\
        --instance-url  https://dev12345.service-now.com \\
        --shared-secret mysecret \\
        --event-type    release \\
        --hook-id       99999

    python spoke_webhook_util.py docusign \\
        --instance-url  https://dev12345.service-now.com \\
        --client-id     <id> --client-secret <secret> \\
        --username      admin --password <pwd>

Stdlib only — no third-party packages required.
Requires Python 3.8+.

No Python?  Install uv (no admin needed) then use  uv run spoke_webhook_util.py
    macOS / Linux :  curl -LsSf https://astral.sh/uv/install.sh | sh
    Windows (PS)  :  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
"""

import argparse
import getpass
import json
import os
import sys
from typing import Dict, Optional

from docusign_spoke_webhook import DocuSignSpokeWebhookUtil
from docusign_spoke_webhook import WebhookResult as _DocuSignResult
from github_spoke_webhook import GitHubSpokeWebhookUtil
from github_spoke_webhook import WebhookResult as _GitHubResult

# ------------------------------------------------------------------ #
# Embedded sample payloads (same JSON as the UI page SAMPLES object) #
# ------------------------------------------------------------------ #

_SAMPLES: Dict[str, dict] = {
    'github': {
        'action': 'created',
        'release': {
            'id': 123456,
            'tag_name': 'v1.0.0',
            'name': 'Release v1.0.0',
            'body': 'First stable release.',
            'draft': False,
            'prerelease': False,
            'created_at': '2026-04-14T09:00:00Z',
            'published_at': '2026-04-14T09:05:00Z',
            'author': {'login': 'octocat', 'id': 1},
        },
        'repository': {
            'id': 654321,
            'name': 'my-integration',
            'full_name': 'acme-corp/my-integration',
            'private': False,
        },
        'sender': {'login': 'octocat', 'id': 1},
    },
    'docusign': {
        'event': 'envelope-completed',
        'apiVersion': 'v2.1',
        'uri': '/restapi/v2.1/accounts/abc123def456/envelopes/env-0001-0002-0003',
        'retryCount': 0,
        'configurationId': 1234567,
        'generatedDateTime': '2026-04-14T09:30:00.000Z',
        'data': {
            'accountId': 'abc123def456',
            'userId': 'user-7890',
            'envelopeId': 'env-0001-0002-0003',
            'envelopeSummary': {
                'status': 'completed',
                'emailSubject': 'Please sign: NDA for Knowledge 2026',
                'createdDateTime': '2026-04-14T09:00:00.000Z',
                'sentDateTime': '2026-04-14T09:01:00.000Z',
                'completedDateTime': '2026-04-14T09:30:00.000Z',
            },
        },
    },
}

_SEP = '─' * 60


# ------------------------------------------------------------------ #
# Output helpers                                                      #
# ------------------------------------------------------------------ #

def _banner() -> None:
    print()
    print('=' * 60)
    print('  Spoke Webhook Simulator  (CCL6454)')
    print('=' * 60)
    print()


def _print_result(result) -> None:
    print()
    print(_SEP)
    if result.success:
        label = 'SUCCESS'
    else:
        label = 'FAILED '
    print(f'  Result    : {label}')
    print(f'  HTTP      : {result.status_code}')
    print(f'  Timestamp : {result.timestamp}')
    if result.error_code:
        print(f'  Error     : {result.error_code}')
        print(f'  Message   : {result.error_message}')
    print(_SEP)
    if result.response_body:
        print('  Response body:')
        try:
            pretty = json.dumps(json.loads(result.response_body), indent=2)
            for line in pretty.splitlines():
                print('    ' + line)
        except Exception:
            print('  ' + result.response_body)
    else:
        print('  (no response body)')
    print(_SEP)
    print()


# ------------------------------------------------------------------ #
# Interactive-mode helpers                                            #
# ------------------------------------------------------------------ #

def _prompt(label: str, default: str = '', required: bool = True) -> str:
    display = f'{label} [{default}]: ' if default else f'{label}: '
    while True:
        value = input(display).strip() or default
        if value or not required:
            return value
        print(f'  ✗  {label} is required.')


def _prompt_secret(label: str) -> str:
    while True:
        value = getpass.getpass(f'{label}: ')
        if value:
            return value
        print(f'  ✗  {label} is required.')


def _prompt_payload(provider: str) -> str:
    print()
    print('  Payload options:')
    print('    1. Use built-in sample payload (default)')
    print('    2. Load from a JSON file')
    print('    3. Paste inline JSON')
    choice = input('  Choose [1]: ').strip() or '1'

    if choice == '2':
        path = _prompt('  File path').replace('"', '').replace("'", '')
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                raw = fh.read().strip()
            json.loads(raw)          # validate before sending
            return raw
        except FileNotFoundError:
            print(f'  ✗  File not found: {path}')
            sys.exit(1)
        except json.JSONDecodeError as exc:
            print(f'  ✗  File does not contain valid JSON: {exc}')
            sys.exit(1)

    if choice == '3':
        raw = input('  JSON (single line): ').strip()
        try:
            json.loads(raw)
            return raw
        except json.JSONDecodeError as exc:
            print(f'  ✗  Invalid JSON: {exc}')
            sys.exit(1)

    return json.dumps(_SAMPLES[provider])


def _prompt_additional_headers() -> Optional[Dict[str, str]]:
    print()
    raw = input('  Additional headers — JSON object, or press Enter to skip: ').strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or isinstance(parsed, list):
            raise ValueError('must be a flat JSON object')
        return parsed
    except (json.JSONDecodeError, ValueError) as exc:
        print(f'  ✗  Invalid additional headers ({exc}). Skipping.')
        return None


def _prompt_verify_ssl() -> bool:
    print()
    answer = input('  Skip SSL certificate verification? (y/N): ').strip().lower()
    return answer in ('y', 'yes')


# ------------------------------------------------------------------ #
# Interactive flows                                                   #
# ------------------------------------------------------------------ #

def _run_interactive_github() -> None:
    print()
    print('─── GitHub Configuration ───────────────────────────────────')
    instance_url = _prompt('Instance URL (e.g. https://dev12345.service-now.com)')
    shared_secret = _prompt_secret('Shared Secret')
    event_type = _prompt('Event Type', default='release')
    hook_id = _prompt('Hook ID', default='', required=False)

    payload = _prompt_payload('github')
    additional_headers = _prompt_additional_headers()
    verify_ssl = not _prompt_verify_ssl()
    debug = input('  Enable debug output? (y/N): ').strip().lower() in ('y', 'yes')

    print()
    print('  Sending…')
    util = GitHubSpokeWebhookUtil(instance_url, shared_secret, verify_ssl=verify_ssl, debug=debug)
    result = util.forward_event(payload, event_type, hook_id or '', additional_headers)
    _print_result(result)


def _run_interactive_docusign() -> None:
    print()
    print('─── DocuSign Configuration ──────────────────────────────────')
    instance_url = _prompt('Instance URL (e.g. https://dev12345.service-now.com)')
    client_id = _prompt('Client ID')
    client_secret = _prompt_secret('Client Secret')
    username = _prompt('Username')
    password = _prompt_secret('Password')

    payload = _prompt_payload('docusign')
    additional_headers = _prompt_additional_headers()
    verify_ssl = not _prompt_verify_ssl()
    use_basic_auth = input('  Use Basic auth instead of OAuth? (y/N): ').strip().lower() in ('y', 'yes')
    debug = input('  Enable debug output? (y/N): ').strip().lower() in ('y', 'yes')

    print()
    print('  Sending…')
    util = DocuSignSpokeWebhookUtil(
        instance_url, client_id, client_secret, username, password,
        verify_ssl=verify_ssl, debug=debug, use_basic_auth=use_basic_auth,
    )
    result = util.forward_event(payload, additional_headers)
    _print_result(result)


def _run_interactive() -> None:
    _banner()
    print('  Provider:')
    print('    1. GitHub  (default)')
    print('    2. DocuSign')
    choice = input('  Choose [1]: ').strip() or '1'
    if choice == '2':
        _run_interactive_docusign()
    else:
        _run_interactive_github()


# ------------------------------------------------------------------ #
# CLI mode                                                            #
# ------------------------------------------------------------------ #

def _load_payload(args) -> str:
    """Resolve payload: file > inline JSON > embedded sample."""
    if getattr(args, 'payload_file', None):
        path = args.payload_file
        try:
            with open(path, 'r', encoding='utf-8') as fh:
                raw = fh.read().strip()
            json.loads(raw)
            return raw
        except FileNotFoundError:
            print(f'Error: payload file not found: {path}', file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as exc:
            print(f'Error: payload file is not valid JSON: {exc}', file=sys.stderr)
            sys.exit(1)

    if getattr(args, 'payload_json', None):
        try:
            json.loads(args.payload_json)
            return args.payload_json
        except json.JSONDecodeError as exc:
            print(f'Error: --payload-json is not valid JSON: {exc}', file=sys.stderr)
            sys.exit(1)

    return json.dumps(_SAMPLES[args.provider])


def _parse_additional_headers(raw: Optional[str]) -> Optional[Dict[str, str]]:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or isinstance(parsed, list):
            raise ValueError('must be a flat JSON object')
        return parsed
    except (json.JSONDecodeError, ValueError) as exc:
        print(f'Error: --additional-headers is not a valid JSON object: {exc}', file=sys.stderr)
        sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='spoke_webhook_util.py',
        description='Simulate a GitHub or DocuSign webhook against a ServiceNow Spoke endpoint.',
        epilog=(
            'Run with no arguments for interactive mode.\n\n'
            'No Python?  Install uv (no admin) then:  uv run spoke_webhook_util.py\n'
            '  macOS/Linux : curl -LsSf https://astral.sh/uv/install.sh | sh\n'
            '  Windows(PS) : powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='provider', metavar='{github,docusign}')

    # ── GitHub subcommand ──────────────────────────────────────────────
    gh = sub.add_parser('github', help='Simulate a GitHub webhook event')
    gh.add_argument(
        '--instance-url', required=True,
        help='SN instance base URL, e.g. https://dev12345.service-now.com',
    )
    gh.add_argument('--shared-secret', required=True, help='GitHub webhook shared secret')
    gh.add_argument('--event-type', default='release',
                    help='x-github-event value (default: release)')
    gh.add_argument('--hook-id', default='', help='x-github-hook-id value (optional)')
    gh.add_argument('--payload-file', metavar='PATH', help='Path to a JSON payload file')
    gh.add_argument('--payload-json', metavar='JSON', help='Inline JSON payload string')
    gh.add_argument('--additional-headers', metavar='JSON',
                    help='Extra headers as a flat JSON object, e.g. \'{"x-foo":"bar"}\'')
    gh.add_argument('--no-verify-ssl', action='store_true',
                    help='Skip TLS certificate validation (use for lab/PDI instances)')
    gh.add_argument('--debug', action='store_true',
                    help='Print verbose request/response details')

    # ── DocuSign subcommand ────────────────────────────────────────────
    ds = sub.add_parser('docusign', help='Simulate a DocuSign envelope event')
    ds.add_argument(
        '--instance-url', required=True,
        help='SN instance base URL, e.g. https://dev12345.service-now.com',
    )
    ds.add_argument('--client-id',     default='',
                    help='OAuth client ID (not required with --use-basic-auth)')
    ds.add_argument('--client-secret', default='',
                    help='OAuth client secret (not required with --use-basic-auth)')
    ds.add_argument('--username',      required=True, help='SN username')
    ds.add_argument('--password',      required=True, help='SN password')
    ds.add_argument('--payload-file',  metavar='PATH', help='Path to a JSON payload file')
    ds.add_argument('--payload-json',  metavar='JSON', help='Inline JSON payload string')
    ds.add_argument('--additional-headers', metavar='JSON',
                    help='Extra headers as a flat JSON object, e.g. \'{"x-foo":"bar"}\'')
    ds.add_argument('--no-verify-ssl', action='store_true',
                    help='Skip TLS certificate validation (use for lab/PDI instances)')
    ds.add_argument('--use-basic-auth', action='store_true',
                    help='Authenticate the Spoke request with HTTP Basic auth '
                         '(username:password) instead of OAuth Bearer token. '
                         'Try this if Bearer auth gives 202 but the flow does not trigger.')
    ds.add_argument('--debug', action='store_true',
                    help='Print verbose request/response details')

    return parser


def _run_cli(args: argparse.Namespace) -> None:
    payload       = _load_payload(args)
    extra_headers = _parse_additional_headers(getattr(args, 'additional_headers', None))
    verify_ssl    = not args.no_verify_ssl
    debug         = getattr(args, 'debug', False)

    if args.provider == 'github':
        util = GitHubSpokeWebhookUtil(
            args.instance_url, args.shared_secret,
            verify_ssl=verify_ssl, debug=debug,
        )
        result = util.forward_event(payload, args.event_type, args.hook_id, extra_headers)
    else:
        use_basic_auth = getattr(args, 'use_basic_auth', False)
        if not use_basic_auth and (not args.client_id or not args.client_secret):
            print(
                'Error: --client-id and --client-secret are required unless '
                '--use-basic-auth is set.',
                file=sys.stderr,
            )
            sys.exit(1)
        util = DocuSignSpokeWebhookUtil(
            args.instance_url, args.client_id, args.client_secret,
            args.username, args.password,
            verify_ssl=verify_ssl, debug=debug, use_basic_auth=use_basic_auth,
        )
        result = util.forward_event(payload, extra_headers)

    _print_result(result)
    sys.exit(0 if result.success else 1)


# ------------------------------------------------------------------ #
# Entry point                                                         #
# ------------------------------------------------------------------ #

def main() -> None:
    if len(sys.argv) < 2:
        _run_interactive()
        return

    parser = _build_parser()
    args = parser.parse_args()

    if args.provider is None:
        # Subcommand omitted but other flags were passed — show help.
        parser.print_help()
        sys.exit(1)

    _run_cli(args)


if __name__ == '__main__':
    main()
