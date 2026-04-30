# Spoke Webhook Simulator — CCL6454

Simulates an inbound webhook delivery from **GitHub** or **DocuSign** directly
to a ServiceNow instance's Spoke external trigger endpoint.  Use it to fire
test events against your integration without needing a real GitHub repository
or DocuSign account.

---

## Files

```
k26-scripts/
├── spoke_webhook_util.py                   ← run this
├── github_spoke_webhook.py                 ← GitHub client (imported automatically)
├── docusign_spoke_webhook.py               ← DocuSign client (imported automatically)
└── sample_payloads/
    ├── github_release.json                 ← sample GitHub release event
    └── docusign_envelope_completed.json    ← sample DocuSign envelope-completed event
```

---

## Prerequisites

You need **Python 3.8 or newer**.  No extra packages — the tool uses the
standard library only.

### Check whether Python is already installed

Open a terminal (or PowerShell on Windows) and run:

```
python3 --version
```

If you see `Python 3.x.x` you are ready.  Skip to [Running the tool](#running-the-tool).

### Getting Python without admin rights

| Platform | Easiest no-admin path |
|---|---|
| **macOS** | Run `python3` once in Terminal — it will prompt you to install Xcode Command Line Tools, which includes Python 3. |
| **Windows** | Open the Microsoft Store and search for **Python 3**. Install from there — no admin required. |
| **Linux** | Use [pyenv](https://github.com/pyenv/pyenv): `curl https://pyenv.run | bash` then `pyenv install 3.12 && pyenv global 3.12`. |

### Alternative — use `uv` (installs Python automatically)

If none of the above works, `uv` can download and manage Python for you in
your home directory — no admin, no system changes.

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Then replace every `python3` command in this guide with `uv run`.

---

## Running the tool

All commands assume your terminal is in the `k26-scripts/` directory.

```bash
cd path/to/k26-scripts
```

---

### Interactive mode (recommended for workshops)

Run with no arguments and the tool will guide you through every step:

```bash
python3 spoke_webhook_util.py
```

```
============================================================
  Spoke Webhook Simulator  (CCL6454)
============================================================

  Provider:
    1. GitHub  (default)
    2. DocuSign
  Choose [1]:
```

You will be prompted for:
- Instance URL
- Credentials / shared secret
- Payload (built-in sample, file, or paste inline)
- Optional additional headers
- Whether to skip SSL verification (needed for some lab instances)

Sensitive inputs (secrets and passwords) are masked — they will not echo to
the screen.

---

### CLI mode (scripted / headless)

#### GitHub

```bash
python3 spoke_webhook_util.py github \
    --instance-url  https://dev12345.service-now.com \
    --shared-secret YOUR_SHARED_SECRET \
    --event-type    release \
    --hook-id       99999
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--instance-url` | Yes | — | SN instance base URL |
| `--shared-secret` | Yes | — | GitHub webhook shared secret |
| `--event-type` | No | `release` | Value for `x-github-event` |
| `--hook-id` | No | _(empty)_ | Value for `x-github-hook-id` |
| `--payload-file` | No | built-in sample | Path to a JSON file |
| `--payload-json` | No | built-in sample | Inline JSON string |
| `--additional-headers` | No | _(none)_ | Extra headers as flat JSON object |
| `--no-verify-ssl` | No | _(verify)_ | Skip TLS certificate check |

#### DocuSign

```bash
python3 spoke_webhook_util.py docusign \
    --instance-url  https://dev12345.service-now.com \
    --client-id     YOUR_CLIENT_ID \
    --client-secret YOUR_CLIENT_SECRET \
    --username      admin \
    --password      YOUR_PASSWORD
```

| Flag | Required | Default | Description |
|---|---|---|---|
| `--instance-url` | Yes | — | SN instance base URL |
| `--client-id` | Yes | — | OAuth client ID |
| `--client-secret` | Yes | — | OAuth client secret |
| `--username` | Yes | — | SN username for OAuth password grant |
| `--password` | Yes | — | SN password for OAuth password grant |
| `--payload-file` | No | built-in sample | Path to a JSON file |
| `--payload-json` | No | built-in sample | Inline JSON string |
| `--additional-headers` | No | _(none)_ | Extra headers as flat JSON object |
| `--no-verify-ssl` | No | _(verify)_ | Skip TLS certificate check |

---

## Payloads

### Built-in samples

If no payload option is given, the tool sends the sample payload embedded in
the script (identical to what the UI page uses).  The JSON files in
`sample_payloads/` are the same content for reference.

### Load from file

```bash
--payload-file sample_payloads/github_release.json
```

The file must be valid JSON. The tool validates it before sending.

### Inline JSON

```bash
--payload-json '{"action":"published","release":{"tag_name":"v2.0.0"}}'
```

### Editing a payload (interactive mode)

Choose option **2 — Load from file**, make a copy of a sample file, edit it
in any text editor, then point the tool at your copy:

```bash
cp sample_payloads/github_release.json my_payload.json
# edit my_payload.json
python3 spoke_webhook_util.py github \
    --instance-url https://dev12345.service-now.com \
    --shared-secret secret \
    --payload-file my_payload.json
```

---

## Additional headers

Both providers accept optional extra headers merged into the Spoke request.
Pass them as a flat JSON object (string keys and string values):

```bash
--additional-headers '{"x-github-delivery":"abc-123","x-custom":"demo"}'
```

---

## SSL / TLS on lab instances

Personal Developer Instances (PDIs) used in workshops sometimes have
self-signed certificates or non-standard TLS configurations.  If you see an
SSL error, add `--no-verify-ssl` (CLI) or answer `y` to the SSL prompt
(interactive mode):

```bash
python3 spoke_webhook_util.py github \
    --instance-url  https://dev12345.service-now.com \
    --shared-secret secret \
    --event-type    release \
    --no-verify-ssl
```

> Do not use `--no-verify-ssl` against production instances.

---

## Reading the output

```
────────────────────────────────────────────────────────────
  Result    : SUCCESS
  HTTP      : 200
  Timestamp : 2026-04-29T10:30:00.123456+00:00
────────────────────────────────────────────────────────────
  Response body:
    {
      "result": { ... }
    }
────────────────────────────────────────────────────────────
```

A `SUCCESS` result means the HTTP call to the Spoke endpoint completed
without a network error.  The actual HTTP status code tells you whether
the Spoke accepted the event:

| HTTP | Meaning |
|---|---|
| `200` | Event accepted and queued by the Spoke trigger |
| `401` | Authentication problem (bad token / wrong credentials) |
| `404` | Spoke plugin not installed or wrong instance URL |
| `5xx` | Instance-side error — check instance logs |

---

## Error codes

These appear in the output when the tool itself cannot complete the request:

| Code | Cause | Fix |
|---|---|---|
| `INVALID_CONFIG` | A required field is missing | Check all required flags / prompts |
| `INVALID_PAYLOAD` | Payload is empty or not a string | Verify your JSON file or inline value |
| `INVALID_EVENT_TYPE` | GitHub `--event-type` is empty | Provide a value such as `release` |
| `SIGNATURE_ERROR` | HMAC-SHA256 computation failed | Check the shared secret for unusual characters |
| `TOKEN_REQUEST_ERROR` | Network error reaching `/oauth_token.do` | Check instance URL and network connectivity |
| `TOKEN_RESPONSE_ERROR` | Non-200 from `/oauth_token.do` | Verify client ID, client secret, username, password |
| `TOKEN_PARSE_ERROR` | Token response was not valid JSON | The instance may not have OAuth configured |
| `SPOKE_REQUEST_ERROR` | Network error reaching the Spoke endpoint | Check instance URL, firewall, and VPN |

---

## How it works

```
spoke_webhook_util.py
    │
    ├─► GitHubSpokeWebhookUtil
    │       1. Compute HMAC-SHA256(payload, sharedSecret)  →  hex digest
    │       2. POST <instance>/api/sn_github_spoke/github_external_trigger
    │              x-hub-signature-256: sha256=<hex>
    │              x-github-event: <eventType>
    │              x-github-hook-id: <hookId>
    │
    └─► DocuSignSpokeWebhookUtil
            1. POST <instance>/oauth_token.do               →  Bearer token
                   grant_type=password, client_id, client_secret, username, password
            2. POST <instance>/api/sn_docusign_spoke/docusign_esignature_external_trigger
                   Authorization: Bearer <token>
```

This mirrors exactly what the CCL6454-Webhooks UI page does via GlideAjax,
but runs entirely from your local machine.
