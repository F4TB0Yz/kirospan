# KiroSpan

An unofficial local gateway that exposes the Kiro runtime through OpenAI- and Anthropic-compatible HTTP APIs. It lets clients such as Hermes Agent, Claude Code, OpenCode, and OpenAI-compatible SDKs use the models available to an authenticated local Kiro CLI installation.

> [!IMPORTANT]
> This project is independent and is not affiliated with, endorsed by, or sponsored by Amazon, AWS, Anthropic, OpenAI, or Kiro. You are responsible for complying with the terms that apply to your Kiro account and clients.

## Features

- OpenAI-compatible `/v1/chat/completions` and `/v1/models` endpoints.
- Anthropic-compatible `/v1/messages` and `/v1/messages/count_tokens` endpoints.
- Streaming text, tool calls, fragmented tool arguments, and parallel tool results.
- Tested Hermes Agent support, including complete tool-call round trips.
- Claude Code subagent and tool-result continuity.
- Base64 PNG, JPEG, GIF, and WebP input images.
- Automatic Kiro access-token refresh and persistence.
- Model-ID normalization, including Claude Code's `[1m]` modifier.
- API-key authentication with constant-time comparison.
- Clean Architecture with framework-independent domain and application layers.

## Security model

The gateway can use the credentials of your local Kiro CLI installation. Treat it as a privileged local process.

- It binds to `127.0.0.1:18789` by default.
- `KIRO_GATEWAY_KEY` is always required; there is no built-in key.
- Do not expose it to a LAN or the internet without TLS, firewall rules, and a deliberate threat review.
- Never commit `.env`, Kiro SQLite databases, Keychain exports, logs, or real API keys.
- Remote image URLs are rejected rather than downloaded by the gateway.

See [SECURITY.md](SECURITY.md) for the complete guidance.

## Requirements

- Python 3.11 or newer.
- A working `kiro-cli` installation authenticated with `kiro-cli auth login`.
- macOS is the primary tested platform. Linux's standard Kiro CLI SQLite location is also recognized; the Keychain fallback is macOS-specific.

## Installation

```bash
cd kirospan
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Authenticate Kiro if necessary:

```bash
kiro-cli auth login
```

Generate a strong local gateway key and start the server:

```bash
export KIRO_GATEWAY_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
./start.sh
```

The application does not automatically load `.env`. If you maintain local environment variables in a file, load them explicitly in your shell and never commit that file.

Verify the process:

```bash
curl http://127.0.0.1:18789/health
curl -H "Authorization: Bearer $KIRO_GATEWAY_KEY" \
  http://127.0.0.1:18789/v1/models
```

## Client configuration

### OpenAI-compatible clients

Use:

```text
base_url = http://127.0.0.1:18789/v1
api_key  = value of KIRO_GATEWAY_KEY
```

Python example:

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:18789/v1",
    api_key=os.environ["KIRO_GATEWAY_KEY"],
)

response = client.chat.completions.create(
    model="claude-sonnet-5",
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.choices[0].message.content)
```

The `openai` package is a client-side option and is not required by the gateway itself.

### Claude Code / Anthropic

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:18789
export ANTHROPIC_API_KEY="$KIRO_GATEWAY_KEY"
claude --model claude-sonnet-5
```

Claude Code may append `[1m]` to a selected model to request its one-million-token context mode. The gateway accepts and normalizes that modifier before calling Kiro.

### OpenCode

Configure an `@ai-sdk/openai-compatible` provider with:

```json
{
  "provider": {
    "kirospan": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "KiroSpan",
      "options": {
        "baseURL": "http://127.0.0.1:18789/v1",
        "apiKey": "replace-with-your-local-gateway-key"
      },
      "models": {
        "claude-sonnet-5": { "name": "Claude Sonnet 5" }
      }
    }
  }
}
```

Keep the real key in your client's secret or environment mechanism; do not commit it in configuration.

### Hermes Agent

Use a dedicated profile so KiroSpan remains optional and does not replace your normal Hermes provider or model:

```bash
./examples/setup-hermes-profile.sh
export KIROSPAN_API_KEY="$KIRO_GATEWAY_KEY"
hermes --profile kirospan -z 'Reply exactly HERMES_KIROSPAN_OK'
```

The helper creates `~/.hermes/profiles/kirospan` with:

- provider `custom:kirospan`;
- model `claude-sonnet-5`;
- OpenAI-compatible transport `chat_completions`;
- endpoint `http://127.0.0.1:18789/v1`;
- `key_env: KIROSPAN_API_KEY`, with no inline API key.

The active Hermes profile and its default provider remain unchanged. To configure the profile manually, run the commands shown in [`examples/setup-hermes-profile.sh`](examples/setup-hermes-profile.sh).

## HTTP API

| Method | Endpoint | Authentication | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health` | No | Process and local credential status |
| `GET` | `/v1/props` | Yes | Gateway capabilities |
| `GET` | `/v1/models` | Yes | Models available from Kiro |
| `POST` | `/v1/chat/completions` | Yes | OpenAI-compatible chat |
| `POST` | `/v1/messages` | Yes | Anthropic-compatible messages |
| `POST` | `/v1/messages/count_tokens` | Yes | Approximate Anthropic token count |

Authentication accepts either `Authorization: Bearer <key>` or `x-api-key: <key>`.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `KIRO_GATEWAY_KEY` | required | Local client authentication key |
| `KIRO_GATEWAY_HOST` | `127.0.0.1` | Listen address |
| `KIRO_GATEWAY_PORT` | `18789` | Listen port |
| `KIRO_REGION` | `us-east-1` | Kiro service region |
| `KIRO_RUNTIME_TIMEOUT` | `120` | Runtime timeout in seconds |
| `KIRO_MODEL_CACHE_TTL` | `300` | Model catalog cache lifetime in seconds |

## Images

Current user messages may contain up to 10 base64 images. Each decoded image must be smaller than 10 MB and use PNG, JPEG/JPG, GIF, or WebP. Historical images are not resent to Kiro. Remote image URLs are intentionally unsupported.

## Development and tests

```bash
python -m pytest -m 'not e2e'
python -m py_compile kiro_gateway.py
```

Run the live test only against a deliberately started local gateway:

```bash
export KIRO_GATEWAY_KEY='your-local-key'
python -m pytest -m e2e tests/test_e2e_live_gateway.py
```

Architecture and dependency rules are documented in [docs/architecture.md](docs/architecture.md). Contribution guidance is in [CONTRIBUTING.md](CONTRIBUTING.md).

## Attribution

The initial Kiro protocol implementation was based on and informed by [`mikeyobrien/pi-provider-kiro`](https://github.com/mikeyobrien/pi-provider-kiro), an MIT-licensed provider for the Pi coding agent. This repository is an independent Python/FastAPI adaptation with additional OpenAI, Anthropic, tool, image, token-refresh, and client-compatibility layers.

See [NOTICE.md](NOTICE.md) for third-party attribution.

## License

MIT. See [LICENSE](LICENSE).
