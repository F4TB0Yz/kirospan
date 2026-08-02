# Changelog

All notable public changes to KiroSpan are documented here.

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
