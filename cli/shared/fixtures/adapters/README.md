# Adapter probe fixtures

These fixtures are provider-free launch-plan inputs. They pin argument
boundaries, environment-name selection, stdin framing, prompt-file bytes, and
terminal records for adapter builders. Paths use literal `{{...}}` placeholders;
substitution is mechanical and never uses a shell.

The generated wire files carry the exact staged `echo-v0` model-visible bytes
and a hostile opaque task envelope. They exercise the same functional-alpha
launch and terminal-carriage boundary as a public artifact, but the fixtures
and fake executable themselves are test material and are never packaged.

`functional-alpha/terminal-carriage.v1.json` gives every product the same
representative Codex, Claude, Prime, and OMP assistant-message stream. The
terminal proves only exact task-argv echoing and transport settlement;
`semanticStatus` remains `not-applicable`.
