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
- Native reasoning output for compatible Kiro models in OpenAI and Anthropic formats.
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

## Responsible use

KiroSpan is an unofficial interoperability tool built on interfaces that may
change or stop working without notice. Use it only with accounts and
credentials you are authorized to control.

- Do not share or resell credentials or gateway access.
- Do not expose it as a public or multi-user service.
- Do not use it to evade quotas, suspensions, access controls, or provider
  policies.
- Review and follow the terms and policies that apply to your account and clients.

## Requirements

- Python 3.11 or newer.
- A working `kiro-cli` installation authenticated with `kiro-cli auth login`.
- macOS is the primary tested platform. Linux's standard Kiro CLI SQLite location is also recognized; the Keychain fallback is macOS-specific.

## Choose a setup

| Goal | Start here |
| --- | --- |
| Use Hermes Agent on macOS | [Quick installation for Hermes Agent](#quick-installation-for-hermes-agent-macos) |
| Use Claude Code, OpenCode, or an SDK | [Manual/development installation](#manualdevelopment-installation) |
| Call the HTTP endpoints directly | [API reference](docs/api.md) |
| Diagnose installation, auth, or model issues | [Troubleshooting](docs/troubleshooting.md) |
| Contribute code | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Quick installation for Hermes Agent (macOS)

For the intended Hermes experience, install KiroSpan once and then use the normal global `hermes` command from any directory:

```bash
kiro-cli auth login
./install-hermes.sh
```

The installer:

- installs KiroSpan in an isolated virtual environment under `~/.local/share/kirospan`;
- runs it automatically as the `dev.kirospan.gateway` macOS LaunchAgent on `127.0.0.1:18789`;
- disables the legacy `com.kiro.gateway` LaunchAgent if present, preserving its plist with a `.disabled-by-kirospan` suffix;
- removes the obsolete Hermes `kiro-gateway` custom provider and preserves a `config.yaml.bak-kirospan` backup;
- synchronizes all models returned by Kiro into the picker while keeping live discovery enabled;
- generates a private gateway key and stores the service copy with mode `0600`;
- registers KiroSpan in the global `~/.hermes/config.yaml` model picker;
- stores `KIROSPAN_API_KEY` through Hermes' global secret store (`~/.hermes/.env`);
- does **not** replace the currently selected Hermes provider or model.

Select a model once:

```bash
hermes model
```

Choose **KiroSpan**, then choose any model returned by Kiro's live model catalog. After that, start Hermes normally from any project or directory:

```bash
hermes
```

No `--profile`, wrapper script, working-directory change, or manual environment export is required. Run `./install-hermes.sh` again to update the installed runtime while preserving the generated key.

Check the background service or restart it with:

```bash
launchctl print "gui/$(id -u)/dev.kirospan.gateway"
launchctl kickstart -k "gui/$(id -u)/dev.kirospan.gateway"
```

Logs are written to `~/Library/Logs/KiroSpan.log` and `~/Library/Logs/KiroSpan.error.log`. The installer currently automates the background service on macOS; on another operating system, use `./install-hermes.sh --no-service` and manage the `kirospan` process with your system service manager.

## Manual/development installation

Use this flow when developing KiroSpan or configuring a client other than Hermes:

```bash
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
kirospan
```

`./start.sh` is also available for repository-local development. It activates
`.venv` or `venv` and runs the compatibility entry point.

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

The recommended integration is global: run [`install-hermes.sh`](install-hermes.sh), select **KiroSpan** with `hermes model`, and then launch `hermes` normally from any directory. Hermes discovers available models from the authenticated `http://127.0.0.1:18789/v1/models` endpoint, so the picker follows Kiro's live catalog instead of relying on a hard-coded model list.

The installer only adds `providers.kirospan` and its secret; it intentionally leaves `model.default` and `model.provider` untouched until you choose KiroSpan yourself. The isolated profile helper in [`examples/setup-hermes-profile.sh`](examples/setup-hermes-profile.sh) remains available for users who explicitly prefer profile isolation, but it is not required for normal use.

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
See [docs/api.md](docs/api.md) for request examples, accepted fields, streaming behavior, reasoning controls, and error semantics.

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

## Reasoning / thinking

KiroSpan forwards reasoning emitted by compatible Kiro models without mixing it
into visible answer text:

- OpenAI streaming responses use `choices[].delta.reasoning_content`;
- OpenAI non-streaming responses use `choices[].message.reasoning_content`;
- Anthropic responses use native `thinking` blocks and `thinking_delta` events.

OpenAI requests may set `reasoning_effort`. Anthropic requests may set
`thinking` and `output_config.effort`. Supported effort values are `low`,
`medium`, `high`, `xhigh`, and `max`; `minimal` is treated as `low`.
Availability still depends on the selected model and the events returned by
Kiro. See [Reasoning and thinking](docs/api.md#reasoning-and-thinking) for the
exact mappings and disable behavior.

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

Architecture and dependency rules are documented in [docs/architecture.md](docs/architecture.md). Contribution guidance is in [CONTRIBUTING.md](CONTRIBUTING.md). For common runtime failures, see [docs/troubleshooting.md](docs/troubleshooting.md).

## Attribution

The initial Kiro protocol implementation was based on and informed by [`mikeyobrien/pi-provider-kiro`](https://github.com/mikeyobrien/pi-provider-kiro), an MIT-licensed provider for the Pi coding agent. This repository is an independent Python/FastAPI adaptation with additional OpenAI, Anthropic, tool, image, token-refresh, and client-compatibility layers.

See [NOTICE.md](NOTICE.md) for third-party attribution.

## License

MIT. See [LICENSE](LICENSE).
