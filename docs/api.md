# HTTP API reference

KiroSpan exposes OpenAI Chat Completions and Anthropic Messages over one local
HTTP server.

## Connection and authentication

Default addresses:

```text
Server:        http://127.0.0.1:18789
OpenAI base:   http://127.0.0.1:18789/v1
Anthropic base:http://127.0.0.1:18789
```

Every endpoint except `GET /health` requires the key configured in
`KIRO_GATEWAY_KEY`. Send it using either header:

```http
Authorization: Bearer your-gateway-key
```

```http
x-api-key: your-gateway-key
```

## Endpoints

| Method | Endpoint | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/health` | No | Process and local Kiro credential status |
| `GET` | `/v1/props` | Yes | Gateway API version and features |
| `GET` | `/v1/models` | Yes | OpenAI-style model catalog |
| `POST` | `/v1/chat/completions` | Yes | OpenAI-compatible chat |
| `POST` | `/v1/messages` | Yes | Anthropic-compatible messages |
| `POST` | `/v1/messages/count_tokens` | Yes | Approximate Anthropic input-token count |

## Health and models

Check the process before diagnosing a client:

```bash
curl -sS http://127.0.0.1:18789/health
```

Example response:

```json
{
  "status": "ok",
  "authenticated": true,
  "token_in_db": true,
  "region": "us-east-1",
  "management_url": "https://management.us-east-1.kiro.dev",
  "runtime_url": "https://runtime.us-east-1.kiro.dev/generateAssistantResponse"
}
```

`authenticated` means KiroSpan found local Kiro credentials. It does not test a
generation request. The endpoint intentionally omits the credential database's
absolute path.

List models with authenticated live discovery:

```bash
curl -sS \
  -H "Authorization: Bearer $KIRO_GATEWAY_KEY" \
  http://127.0.0.1:18789/v1/models
```

Use a model `id` returned by this endpoint. Model aliases and Claude Code's
`[1m]` suffix are normalized before the upstream request.

## OpenAI Chat Completions

Minimal non-streaming request:

```bash
curl -sS http://127.0.0.1:18789/v1/chat/completions \
  -H "Authorization: Bearer $KIRO_GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-5",
    "messages": [
      {"role": "user", "content": "Reply with one short sentence."}
    ]
  }'
```

Set `"stream": true` to receive Server-Sent Events ending in `data: [DONE]`.
Supported top-level request fields are:

- `model` and `messages` (required);
- `stream`, `max_tokens`, and `temperature`;
- `tools` and `tool_choice`;
- `reasoning_effort`.

The gateway accepts the standard text, tool-call, tool-result, and supported
base64 image message forms used by compatible clients.

`max_tokens`, `temperature`, and `tool_choice` are accepted for client-schema
compatibility, but are not currently translated into Kiro runtime controls.

## Anthropic Messages

Minimal non-streaming request:

```bash
curl -sS http://127.0.0.1:18789/v1/messages \
  -H "x-api-key: $KIRO_GATEWAY_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-5",
    "max_tokens": 512,
    "messages": [
      {"role": "user", "content": "Reply with one short sentence."}
    ]
  }'
```

Set `"stream": true` for Anthropic-style Server-Sent Events. Supported
top-level request fields are:

- `model` and `messages` (required);
- `system`, `stream`, `max_tokens`, and `temperature`;
- `tools` and `tool_choice`;
- `thinking` and `output_config`.

`max_tokens`, `temperature`, and `tool_choice` are accepted for client-schema
compatibility, but are not currently translated into Kiro runtime controls.

Kiro does not expose a tokenizer endpoint. Consequently,
`POST /v1/messages/count_tokens` returns an estimate based on text length and
image count; do not use it as an exact billing value or hard context-limit
guarantee.

## Reasoning and thinking

Reasoning controls are translated into Kiro's model-specific request fields.

| Client input | Accepted behavior |
| --- | --- |
| OpenAI `reasoning_effort` | Accepts `low`, `medium`, `high`, `xhigh`, or `max` |
| Anthropic `output_config.effort` | Accepts the same effort values |
| Anthropic `thinking.type: "enabled"` or `"adaptive"` | Enables reasoning with `medium` effort when no effort is supplied |
| `minimal` effort | Normalized to `low` |
| `none`, `off`, or `thinking.type: "disabled"` | Disables the extra reasoning request fields |

For GPT model IDs, controls become Kiro's `reasoning.effort`; `max` is capped
to `xhigh`. For Claude model IDs, controls become adaptive thinking plus
`output_config.effort`. Unsupported effort strings or unrelated model families
do not add reasoning fields.

When Kiro emits reasoning:

- OpenAI streams expose `choices[].delta.reasoning_content`;
- OpenAI non-streaming responses expose
  `choices[].message.reasoning_content`;
- Anthropic streams expose `thinking` content blocks with `thinking_delta`
  and signature events;
- Anthropic non-streaming responses expose `thinking` content blocks.

Reasoning is model- and upstream-dependent. Requesting an effort does not
guarantee that a response contains reasoning.

## Images

Images must be caller-provided base64 content. Remote URLs are rejected.

- Formats: PNG, JPEG/JPG, GIF, and WebP.
- Maximum images in the current user turn: 10.
- Maximum decoded size per image: less than 10 MB.
- Historical images are not resent to Kiro.

## Tools

Tool definitions are forwarded to Kiro and tool calls are returned in the
selected client protocol. KiroSpan never executes a tool. The calling client is
responsible for execution and for sending tool results in the next request.

Streaming supports fragmented tool arguments and multiple tool calls. Preserve
the tool-call ID when returning a result so conversation continuity remains
intact.

## Errors

| Status or transport | Meaning |
| --- | --- |
| `400` | Invalid message, tool, image, or conversation shape |
| `401` with `Invalid API key` | Missing or incorrect `KIRO_GATEWAY_KEY` |
| `401` with `Credenciales Kiro no disponibles` | Local Kiro credentials are missing or unusable |
| `422` | Request body failed schema validation |
| `502` | Upstream failure without a more specific status |
| `504` | Kiro runtime timeout |

Non-streaming calls preserve a specific upstream HTTP status when available.
Once a streaming response has started, failures are serialized in the stream
because its HTTP status has already been sent.

See [troubleshooting.md](troubleshooting.md) for a diagnostic sequence.
