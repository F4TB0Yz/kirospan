# Security Policy

## Supported versions

Security fixes are applied to the latest `1.x` release and the current default branch. Older snapshots are not supported.

## Reporting a vulnerability

Use the repository's private security-advisory feature when available. If it is not available, contact the maintainer privately before opening a public issue. Do not include Kiro tokens, refresh tokens, API keys, SQLite databases, Keychain output, full request bodies, or unredacted logs in a report.

A useful report contains:

- affected version or commit;
- impact and attack prerequisites;
- minimal reproduction using synthetic credentials;
- suggested mitigation, if known.

## Threat model

This gateway can act with the local user's Kiro credentials. Its primary security boundary is the local machine:

- The default bind is `127.0.0.1`; keep it that way unless remote access is intentionally secured.
- Every endpoint except `/health` requires `KIRO_GATEWAY_KEY`.
- Use a randomly generated key and provide it to clients through environment or secret-management facilities.
- Loopback does not protect against other processes running as the same user. Treat untrusted local software as capable of reaching local ports.
- A public bind requires external TLS termination, strict firewall rules, key rotation, and rate limiting. Those controls are outside this repository.

## Credential handling

The gateway reads the Kiro CLI credential database and, on macOS, may use the Keychain fallback provided by the `security` command. It refreshes and persists access tokens when needed.

Never commit or upload:

- `.env` files;
- `data.sqlite3` or any Kiro credential database;
- Keychain exports;
- gateway API keys;
- access or refresh tokens;
- `stdout.log`, `stderr.log`, or request captures containing user data.

The repository `.gitignore` excludes common forms of these files, but contributors must still inspect staged changes before publishing.

## Network and content behavior

- Upstream requests are sent only to region-derived Kiro management, runtime, and desktop-auth endpoints.
- Remote image URLs are rejected; only caller-provided base64 data is processed.
- `/health` intentionally omits the absolute credential-database path.
- Upstream error bodies and stream exception messages are not returned to
  clients or written to logs. Status codes and request IDs are retained for
  diagnosis.
- Client-provided tools are descriptions only: the gateway does not execute tool calls. Execution happens in the calling client.

## Dependency updates

Review dependency release notes and lock deployment environments as appropriate. Do not install similarly named or unverified packages solely because a test suggests them.
