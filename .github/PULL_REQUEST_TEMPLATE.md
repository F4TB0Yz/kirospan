## Summary

Describe the problem and the smallest change that solves it.

## Verification

List targeted tests and the complete non-E2E test result.

## Checklist

- [ ] I added or updated focused regression tests when behavior changed.
- [ ] I ran `python -m pytest -m 'not e2e'` or explained why it was not applicable.
- [ ] I kept credentials, prompts, model output, personal paths, and generated files out of this PR.
- [ ] I preserved the dependency boundaries documented in `docs/architecture.md`.
- [ ] I updated public documentation for behavior, security, or compatibility changes.
- [ ] This change does not bypass quotas, suspensions, access controls, or provider policies.

## Security impact

Describe changes to authentication, credentials, logging, network exposure, or
write "None".
