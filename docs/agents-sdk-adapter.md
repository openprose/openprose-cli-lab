# Agents SDK adapter

Both runners accept `--harness agents-sdk --model MODEL --auth-profile openai-api-key --output-contract native`. Put `prose-agents-sdk` on PATH. It is the generic Python harness in `harnesses/agents-sdk/run.py`, with dependencies pinned in that directory. It receives an opaque instructions file, opaque task prompt and working directory. The model chooses ordinary shell tool calls; neither adapter nor harness interprets Contracts or OpenProse syntax.

Use a private virtual environment and expose an executable copy of run.py with its interpreter's absolute shebang. The lab has this at `build/harness-bin/prose-agents-sdk`; its virtual environment is `harnesses/agents-sdk/.venv`. These are generated development artifacts, not language dependencies.

An explicit model and nonempty OPENAI_API_KEY are required. There is no cached-login route. The shell tool receives a scrubbed environment without credential-like variables; this is not filesystem confinement. Native events are start, tool_call, tool_result, final, or error. Only final settles a successful native run. A final result does not establish program fulfillment.

Observed development checks: Rust and Bun with both gpt-5.4-mini and gpt-5.5 read a fresh random input and wrote an exact output artifact. Evidence and executable hashes are recorded separately in lab/compatibility/sdk-native/results.json. These canaries validate the transport and tool path, not general language conformance.
