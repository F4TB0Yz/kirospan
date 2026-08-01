# Troubleshooting

Use this sequence to separate process, gateway-key, Kiro-auth, and client
configuration failures.

## 1. Check the process

```bash
curl -sS http://127.0.0.1:18789/health
```

Expected: HTTP 200 with `"status": "ok"`.

If the connection is refused:

- manual install: activate the virtual environment, export
  `KIRO_GATEWAY_KEY`, then run `kirospan`;
- Hermes macOS install: inspect the LaunchAgent and logs:

  ```bash
  launchctl print "gui/$(id -u)/dev.kirospan.gateway"
  tail -n 100 ~/Library/Logs/KiroSpan.error.log
  ```

- check whether another process owns the default port:

  ```bash
  lsof -nP -iTCP:18789 -sTCP:LISTEN
  ```

Change `KIRO_GATEWAY_PORT` in both gateway and client if the port must differ.

## 2. Check local Kiro credentials

In `/health`, `"authenticated": false` means no usable local token was found.
Authenticate again:

```bash
kiro-cli auth login
```

Then retry `/health` and `/v1/models`. On macOS, KiroSpan can fall back to the
Kiro Keychain entry; Linux uses the recognized Kiro CLI SQLite location.

An authenticated API request that returns:

```json
{"detail": "Credenciales Kiro no disponibles"}
```

also indicates missing, expired, or unrefreshable Kiro credentials. The generic
message intentionally avoids exposing token or filesystem details.

## 3. Check the gateway key

```bash
curl -i \
  -H "Authorization: Bearer $KIRO_GATEWAY_KEY" \
  http://127.0.0.1:18789/v1/models
```

`401` with `Invalid API key` means the client key differs from the gateway's
`KIRO_GATEWAY_KEY`. Common causes:

- the key was generated in one shell but the gateway runs in another;
- Hermes uses `KIROSPAN_API_KEY`, while the manual gateway uses
  `KIRO_GATEWAY_KEY`;
- a copied key includes quotes, whitespace, or a newline;
- the LaunchAgent has a different installed key than the current shell.

For the global Hermes installation, rerun `./install-hermes.sh`. It preserves
the installed key and refreshes provider configuration.

## 4. Check client base URLs

Use the exact base expected by each client:

| Client protocol | Base URL |
| --- | --- |
| OpenAI-compatible | `http://127.0.0.1:18789/v1` |
| Anthropic / Claude Code | `http://127.0.0.1:18789` |
| Hermes KiroSpan provider | `http://127.0.0.1:18789/v1` |

Adding `/v1` twice commonly produces a `404`. Omitting it in an
OpenAI-compatible client commonly targets the wrong path.

## 5. Check model discovery

```bash
curl -sS \
  -H "Authorization: Bearer $KIRO_GATEWAY_KEY" \
  http://127.0.0.1:18789/v1/models
```

Copy a returned `id` into a minimal request from [api.md](api.md). If direct
`curl` works but a client does not, the remaining problem is client
configuration.

For Hermes:

```bash
hermes model
```

Select **KiroSpan**, then select a discovered model. The installer deliberately
does not replace the active provider or model.

## Common errors

### `KIRO_GATEWAY_KEY is required`

Generate and export a key before starting a manual instance:

```bash
export KIRO_GATEWAY_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
kirospan
```

KiroSpan does not load `.env` automatically.

### `ModuleNotFoundError`

Use the project environment and install all package dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

### `400` on an image request

Confirm that the image is base64 data, not a remote URL; decoded size is under
10 MB; format is PNG, JPEG, GIF, or WebP; and the current user turn contains no
more than 10 images.

### Reasoning is absent

Reasoning output depends on both selected model and Kiro's response. Confirm:

- request uses a supported effort value;
- `thinking.type` is not `disabled` or `off`;
- model ID belongs to a supported GPT or Claude family;
- client reads `reasoning_content` or native Anthropic `thinking` blocks.

Visible answer text remains separate from reasoning by design.

### Timeout or intermittent upstream error

Increase `KIRO_RUNTIME_TIMEOUT` for slow generations. KiroSpan retries a safe
transient failure once before public output begins; it does not replay a request
after visible output because that could duplicate content or tool calls.
Public errors and logs omit upstream response bodies. Use the HTTP status and
request ID for correlation; do not add raw bodies when sharing diagnostics.

## Logs and safe reporting

The Hermes LaunchAgent writes:

```text
~/Library/Logs/KiroSpan.log
~/Library/Logs/KiroSpan.error.log
```

Before sharing logs, remove API keys, access or refresh tokens, request bodies,
personal paths, and model output. Follow [SECURITY.md](../SECURITY.md) for
private vulnerability reporting.
