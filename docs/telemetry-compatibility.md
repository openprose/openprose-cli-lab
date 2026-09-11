# Claude thinking-token telemetry compatibility

Pinned Claude Code 2.1.243 emits a session-bound `system/thinking_tokens` informational event after initialization, including for a no-tool echo. Both original parsers rejected it and terminated otherwise valid runs. Direct identical read-file execution succeeded, isolating the failure to transport rather than Markdown interpretation.

The repair admits only the observed six-field shape with the established session identity, a nonempty UUID, and nonnegative safe integer counters. It produces neither assistant text nor a completion signal. Unknown sessions, invalid fields, and missing terminal result remain errors. A shared fixture and independent Rust/Bun tests exercise these boundaries.

This does not change tools, permissions, credentials, language interpretation, fulfillment criteria, or model selection. Initial live probes used installed-login auth; later API-key lanes must explicitly name their credential route.

Rust also has a generic line-framing stage before its semantic transport normalizer. It originally treated every subsequent `system` record as a duplicate start. The framing stage now permits a repeated start event type only when an adapter explicitly lists that type as an allowed later event. Claude does so; its normalizer continues to require first init, matching session, and recognized telemetry subtype. Existing adapters retain strict duplicate-start rejection. Native `tool_progress` is carried consistently with Bun.
