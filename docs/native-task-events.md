# Native Claude task telemetry

Claude 2.1.243 emits session-bound `system` records with task_started, task_progress, task_updated, and task_notification while using native agents. Both adapters preserve these as nonterminal telemetry. A child task completion never establishes outer invocation completion or semantic fulfillment: native outer result is still required.

Shared fixture `claude-task-lifecycle.json` retains observed field shapes from the finance-feedback-v3 rejected task_started event and earlier direct review-v1 task lifecycle events; identities and free text are sanitized. Parsers require active-session identity, task identity, UUID, and subtype-specific structural fields; unknown subtypes remain rejected. The CLI does not interpret task purpose, reviewer decisions, or Markdown.

Native `--bare` sets CLAUDE_CODE_SIMPLE and exposes only Bash/Edit/Read in the tested version, even with an explicit Agent list. Experiments needing native agents used an explicitly recorded nonbare native launcher, fresh CLAUDE_CONFIG_DIR, safe-mode, empty setting sources, ordinary tool list, and authorized directory access. Native init reported ANTHROPIC_API_KEY. This is distinct from the stock API profile, which still includes bare; do not conflate authentication with tool capability.
