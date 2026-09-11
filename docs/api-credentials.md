# Explicit provider API credentials

Use `--harness claude --auth-profile anthropic-api-key` with ANTHROPIC_API_KEY in the child process environment. Only that credential is selected; competing token/provider environment variables are stripped. Missing or empty keys fail before launch. Presence is reported as unknown readiness, not successful authentication.

This route additionally passes native Claude `--bare` alongside `--safe-mode`. Installed pinned 2.1.243 help documents bare mode as excluding OAuth/keychain reads and accepting ANTHROPIC_API_KEY (or explicitly configured helpers); no helper/settings override is supplied here. It is an explicit API-key route, not silent substitution of a user's stored login. The ordinary claude-subscription route is unchanged. Invalid API authentication remains a failure; the runner does not retry through login.

In the tested Claude version, bare also sets CLAUDE_CODE_SIMPLE and restricts native tools to Bash/Edit/Read; it does not expose native delegation even when Agent is requested. This is an auth/context coupling in the current adapter, not a language restriction. Use the explicit [native workspace profile](native-profiles.md) to select the nonbare seven-tool environment with fresh API configuration and observed credential-source verification. Tool permissions remain separate; default behavior is unchanged.

Codex's `openai-api-key` profile selects a native custom provider named `openai-env`, configured for the official Responses API and OPENAI_API_KEY, with native login not required. Fresh CODEX_HOME runs established an API-key path without a cached account store. This does not attest the provider account's billing identity; retain observed runtime evidence. Native skill/context discovery requires separate configuration even with a fresh home.

The optional `agents-sdk` harness requires `openai-api-key` and an explicit model; it has no cached-login route. Prime and OMP require a qualified provider/model and explicit provider-key or harness-login profile. For example, `anthropic` selects ANTHROPIC_API_KEY and `openai` selects OPENAI_API_KEY; provider-key runs receive fresh runner-owned configuration. `prime-harness-login` and `omp-harness-login` instead retain native stores and pass no provider credential variables. Inspect `cli harness list` for the installed adapters and supported routes.

Never put credentials in argv or persist them in reports. Parse the user-designated dotenv file in a parent process and pass only the chosen credential. No shell `source`, key printing, or credentials committed to Git.
