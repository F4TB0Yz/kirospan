# Changelog

All notable public changes to KiroSpan are documented here.

## [1.1.0](https://github.com/F4TB0Yz/kirospan/compare/v1.0.0...v1.1.0) (2026-10-26)


### Features

* add OpenAI Responses API (/v1/responses) for Codex support ([a8bfa9b](https://github.com/F4TB0Yz/kirospan/commit/a8bfa9b7193c88e72e0caef51f5f00f57ae0a862))
* keep-alive SSE, identidad de sesión estable y saneo de tools para Kiro ([3d29fb5](https://github.com/F4TB0Yz/kirospan/commit/3d29fb5d75315eb8b079aca56edbf9302616de7a))


### Bug Fixes

* propagar el filtro de contenido de Kiro como negativa ([3d945ea](https://github.com/F4TB0Yz/kirospan/commit/3d945ea28e5e98db107336b1ac3b946f7f5fa939))

## [1.0.0] - 2026-08-01

First stable public release.

### Added

- OpenAI-compatible chat completions and model discovery.
- Anthropic-compatible messages and token counting.
- Streaming text, tool calls, images, and reasoning output.
- Local gateway authentication with secure loopback defaults.
- Kiro CLI credential loading, refresh, and safe persistence behavior.
- Hermes Agent installer for macOS and client configuration guides.
- Python 3.11 through 3.14 CI, Dependabot, GitGuardian, and GitHub secret
  scanning protections.

### Security

- Upstream response bodies and stream exception details are excluded from
  public errors and logs.
- Public-use warnings, responsible-use guidance, vulnerability reporting, and
  contribution templates are included.

### Platform support

- macOS is supported.
- Linux support is experimental.
- Windows is not supported in this release.
