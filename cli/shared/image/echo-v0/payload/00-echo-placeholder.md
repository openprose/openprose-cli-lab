# OpenProse echo transport placeholder

OPENPROSE_ECHO_IMAGE_V0

This is a deliberately nonsemantic public-alpha placeholder. It verifies that
the OpenProse CLI can launch the explicitly selected installed harness, deliver
an opaque Skill Runtime Image and task envelope, and recover a terminal
envelope. It does not implement, parse, validate, or execute OpenProse.

When you receive the task:

1. Do not interpret the task as an OpenProse program.
2. Do not use tools, run commands, modify files, or delegate to another agent.
3. Echo the supplied task `argv` as plain text, preserving every string and its
   order. The task may arrive as the user message or inside the transport's
   `<task>` element; do not echo the image or transport framing.
4. Copy the task's `argv` field exactly into the terminal object's `task.argv`
   field.
5. Finish with exactly one minified JSON object as the final nonblank line,
   with no Markdown fence, prefix, suffix, or commentary after it. Use the
   actual input `argv`, not the example value. For an input `argv` of
   `["prose","run","hello.prose.md"]`, the exact final line would be:

{"schema":"openprose.echo-terminal/1","semanticStatus":"not-applicable","placeholder":true,"marker":"OPENPROSE_ECHO_TERMINAL_V0","task":{"argv":["prose","run","hello.prose.md"]}}
