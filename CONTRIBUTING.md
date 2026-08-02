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

Documentation changes should keep these sources synchronized:

- endpoint, request, or response changes: `README.md` and `docs/api.md`;
- install, authentication, or runtime changes: `README.md` and
  `docs/troubleshooting.md`;
- layer or dependency changes: `docs/architecture.md`;
- credential or exposure changes: `SECURITY.md`.

Before submitting documentation-only changes, run:

```bash
git diff --check
git status --short
```

Verify every documented command from a clean shell or state the prerequisite
that makes it runnable.

## Style

- Prefer small functions and explicit dependency injection.
- Preserve existing protocol field names at wire boundaries.
- Keep public documentation and error messages understandable without local context.
- Comments should explain constraints and decisions rather than restate code.

## Releases

Release Please prepares version bumps, `CHANGELOG.md`, tags, and GitHub Releases.
Use Conventional Commit prefixes in squash-merge titles:

- `fix:` creates a patch candidate;
- `feat:` creates a minor candidate;
- `feat!:` or `BREAKING CHANGE:` creates a major candidate;
- `docs:` and `deps:` may be included in Python release notes.

The bot opens a release PR; merging that PR publishes the release. Maintainers
must configure the `RELEASE_PLEASE_TOKEN` Actions secret with a fine-grained
token allowed to manage contents and pull requests so CI runs on bot-created
PRs. The workflow falls back to `GITHUB_TOKEN`, but GitHub suppresses CI events
created with that token.

## Security-sensitive changes

Changes to authentication, token persistence, URL construction, public bind behavior, or logging require explicit security tests. Never weaken loopback defaults or introduce a built-in API key for convenience.
