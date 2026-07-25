#!/usr/bin/env bash
# Configure an isolated Hermes Agent profile for KiroSpan.
set -euo pipefail

profile="${HERMES_KIROSPAN_PROFILE:-kirospan}"
base_url="${KIROSPAN_BASE_URL:-http://127.0.0.1:18789/v1}"
model="${KIROSPAN_MODEL:-claude-sonnet-5}"

if [[ ! "$profile" =~ ^[a-z0-9]+$ ]]; then
    echo "HERMES_KIROSPAN_PROFILE must be lowercase and alphanumeric." >&2
    exit 2
fi

if ! command -v hermes >/dev/null 2>&1; then
    echo "Hermes Agent is not installed or is not available on PATH." >&2
    exit 2
fi

profile_dir="${HOME}/.hermes/profiles/${profile}"
if [[ ! -d "$profile_dir" ]]; then
    hermes profile create "$profile" --no-alias --no-skills
fi

hermes --profile "$profile" config set model.default "$model"
hermes --profile "$profile" config set model.provider "custom:${profile}"
hermes --profile "$profile" config set model.base_url "$base_url"
hermes --profile "$profile" config set model.api_mode chat_completions
hermes --profile "$profile" config set "providers.${profile}.name" KiroSpan
hermes --profile "$profile" config set "providers.${profile}.api" "$base_url"
hermes --profile "$profile" config set "providers.${profile}.key_env" KIROSPAN_API_KEY
hermes --profile "$profile" config set "providers.${profile}.default_model" "$model"
hermes --profile "$profile" config set "providers.${profile}.transport" chat_completions

cat <<EOF
Hermes profile '${profile}' is configured for KiroSpan.
No API key was written to the profile.

Before invoking Hermes, export the gateway key for this process:
  export KIROSPAN_API_KEY="\$KIRO_GATEWAY_KEY"
  hermes --profile ${profile} -z 'Reply exactly HERMES_KIROSPAN_OK'
EOF
