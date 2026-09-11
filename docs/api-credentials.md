# Explicit provider API credentials

Use `--harness claude --auth-profile anthropic-api-key` with ANTHROPIC_API_KEY in the child process environment. Only that credential is selected; competing token/provider environment variables are stripped. Missing or empty keys fail before launch. Presence is reported as unknown readiness, not successful authentication.

This route additionally passes native Claude `--bare` alongside `--safe-mode`. Installed pinned 2.1.243 help documents bare mode as excluding OAuth/keychain reads and accepting ANTHROPIC_API_KEY (or explicitly configured helpers); no helper/settings override is supplied here. It is an explicit API-key route, not silent substitution of a user's stored login. The ordinary claude-subscription route is unchanged. Invalid API authentication remains a failure; the runner does not retry through login.

Codex already supports `--auth-profile openai-api-key` and selects OPENAI_API_KEY. That existing route does not prove that an ambient native account store cannot outrank the key; observed provider/billing identity must be reported honestly. Do not infer API billing from selected profile alone. A subsequent isolation improvement can establish stronger evidence.

Never put credentials in argv or persist them in reports. Parse the user-designated dotenv file in a parent process and pass only the chosen credential. No shell `source`, key printing, or credentials committed to Git.
