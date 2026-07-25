# Architecture

Kiro Gateway follows Clean Architecture: policy and protocol transformation stay independent of frameworks, while I/O details live at the edges. Dependencies point inward.

```text
HTTP clients
    |
    v
adapters/inbound/http  --->  protocols/openai + protocols/anthropic
    |                                   |
    v                                   v
application services  ---------->  protocols/kiro
    |
    v
application ports  <----------  adapters/outbound
    |                                  |
    v                                  v
domain rules                  Kiro HTTP / SQLite / Keychain
```

`composition.py` is the composition root that creates concrete adapters and injects them into application services and HTTP routers.

## Layers

### Domain — `src/gateway/domain`

Framework-independent rules and values:

- `model_id.py`: sanitization, aliases, date suffixes, and Claude Code's `[1m]` modifier.
- `token.py`: token field normalization and expiration rules.
- `images.py`: base64 decoding, format/size/count validation, and Kiro image values.
- `events.py`: typed events emitted by the chat pipeline.
- `errors.py`: errors expressed without HTTP framework types.

The domain does not import FastAPI, httpx, SQLite, or concrete adapters.

### Protocol translation — `src/gateway/protocols`

Pure translation between wire contracts:

- `openai/`: model catalogs and streaming/non-streaming presenters.
- `anthropic/`: request conversion, response conversion, SSE presentation, and continuation policy.
- `kiro/`: conversation-state construction, tool specifications, bracket-tool fallback parsing, and runtime event decoding.

Keeping translation outside routers makes protocol behavior testable without a server or network.

### Application — `src/gateway/application`

Use-case orchestration:

- `TokenService` loads, refreshes, and persists credentials with concurrency control.
- `ModelCatalog` resolves profile ARNs and owns the per-instance TTL cache.
- `ChatService` turns decoded upstream events into stable domain events.
- `ports.py` defines structural `Protocol` interfaces for tokens, refresh, runtime streaming, model discovery, and time.

Application services depend on ports and domain rules, never on concrete infrastructure.

### Adapters — `src/gateway/adapters`

Inbound adapters:

- FastAPI schemas, authentication middleware, and routers for OpenAI, Anthropic, and metadata endpoints.

Outbound adapters:

- `HttpKiroRuntimeClient` for generation streams.
- `HttpModelCatalogSource` for profiles and available models.
- `DesktopTokenRefresher` for desktop-auth refresh.
- `SqliteTokenRepository` for Kiro CLI SQLite plus the macOS Keychain fallback.
- AWS EventStream framing and the system clock.

### Composition root — `src/gateway/composition.py`

`create_app(config)` owns concrete construction and lifecycle:

1. create the credential repository;
2. create and close the shared `httpx.AsyncClient` in FastAPI lifespan;
3. build token and model-catalog services;
4. inject runtime factories and services into routers;
5. install authentication middleware.

No other layer decides which concrete adapter is used.

## Request flow

For a streamed chat request:

1. Authentication middleware validates the gateway key.
2. The inbound router converts the OpenAI or Anthropic body to a common message representation.
3. Model IDs and images are validated by domain functions.
4. Kiro protocol builders create the alternating conversation state and tool context.
5. `TokenService` supplies a valid access token and `ModelCatalog` resolves the profile ARN.
6. The outbound runtime adapter streams AWS EventStream frames.
7. `ChatService` emits domain events.
8. The selected OpenAI or Anthropic presenter serializes those events to the client protocol.
9. A single 403 retry may refresh the token and replay the upstream call.

## Compatibility shim

`kiro_gateway.py` remains at the repository root because older integrations and regression tests import and monkeypatch its historical symbols. It delegates production construction to `gateway.composition.create_app` and delegates behavior to the layered implementation.

New production logic belongs under `src/gateway/`. The shim should shrink over time as legacy seams are retired.

## Testing strategy

- Domain tests cover pure rules and validation boundaries.
- Protocol tests use deterministic request/event fixtures and snapshots.
- Application tests inject fake ports and clocks.
- Adapter tests fake HTTP streams, SQLite, subprocesses, and refresh responses.
- Composition tests exercise FastAPI routing, authentication, and lifespan.
- Legacy tests protect compatibility with historical import seams.
- The marked live E2E test is opt-in and requires explicit local credentials.

This distribution keeps the fast suite deterministic and credential-free while preserving one deliberate runtime verification path.
