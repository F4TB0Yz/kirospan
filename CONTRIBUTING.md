# Contributing

Contributions are welcome when they preserve protocol compatibility, security, and the dependency rule described in [docs/architecture.md](docs/architecture.md).

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -m 'not e2e'
```

Unit tests must not require a real Kiro account or network access. Use the fakes and event fixtures under `tests/`. Live tests belong in `tests/test_e2e_live_gateway.py`, must carry the `e2e` marker, and must skip cleanly without explicit environment configuration.

## Architecture rules

- `domain/` contains pure rules and value transformations. It must not import FastAPI, httpx, SQLite, or adapters.
- `application/` coordinates use cases and depends on `Protocol` ports, not concrete infrastructure.
- `protocols/` translates OpenAI, Anthropic, and Kiro wire formats without performing network I/O.
- `adapters/` owns HTTP, SQLite, Keychain, clocks, and Kiro network calls.
- `composition.py` is the composition root and the only place that selects concrete implementations.
- `kiro_gateway.py` is a compatibility shim. New behavior should be implemented in `src/gateway/` and exposed through the shim only when legacy compatibility requires it.

## Change checklist

1. Add a focused regression that fails before the implementation.
2. Keep credentials, personal paths, and machine-specific state out of fixtures.
3. Run targeted tests, then the complete non-E2E suite.
4. Run `python -m py_compile kiro_gateway.py`.
5. Check staged files for secrets and generated artifacts.
6. Update README, architecture notes, or notices when behavior or attribution changes.

## Style

- Prefer small functions and explicit dependency injection.
- Preserve existing protocol field names at wire boundaries.
- Keep public documentation and error messages understandable without local context.
- Comments should explain constraints and decisions rather than restate code.

## Security-sensitive changes

Changes to authentication, token persistence, URL construction, public bind behavior, or logging require explicit security tests. Never weaken loopback defaults or introduce a built-in API key for convenience.
