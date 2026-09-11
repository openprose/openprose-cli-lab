# Explicit permissions and native notices

`--permission-mode acceptEdits` selects Claude's native edit-accepting mode for an invocation. `--permission-mode default` explicitly selects its default. Omitting the option changes nothing. The equivalent environment setting is PROSE_PERMISSION_MODE and the config-file key is permission_mode. Other harnesses reject this currently unmapped option rather than guessing. API credential profiles never select permissions.

Claude emits nonterminal `system/permission_denied` notices. Both parsers now accept valid notices tied to the current session and continue so the agent can recover. A notice is not completion or a fatal outcome by itself. Unknown sessions/malformed notices still fail. The native trace from the first ledger reproduction is retained in the private lab; normal normalized output currently does not expose all native telemetry.

The real ledger reproduction with default permissions failed to write its result. With explicit acceptEdits both products created the requested artifact and reached native settlement, then failed the separate legacy image-envelope requirement because the model did not append its final JSON carrier. Those are distinct observations: native permissions were repaired; no missing envelope was invented, and artifact creation alone does not prove semantic acceptance.
