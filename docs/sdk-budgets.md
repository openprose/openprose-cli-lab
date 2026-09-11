# Agents SDK execution budgets

Both runners accept these optional environment controls for `--harness agents-sdk`:

- `--native-max-turns N`: positive safe integer. Default 20. An SDK turn is one model invocation, including any tool calls; it is not a tool-call count.
- `--native-timeout TIME`: positive duration using `ms`, `s`, `m`, or `h`. Default 180s. This controls the SDK harness's inner whole-run deadline and model request timeout.

- `--native-tool-timeout TIME`: positive duration with the same units. Default 30s. This controls each SDK shell action; an explicit `180s` forwards the existing helper option `--tool-timeout 180`.

`--timeout` remains the independent outer runner deadline. The first applicable limit wins. Setting an outer deadline longer than 180s does not silently extend the SDK deadline. For example, `--timeout 10m --native-timeout 8m --native-max-turns 60` grants up to 60 SDK turns and 480 seconds inside the 600-second outer limit. These flags do not guarantee completion, add retries, or change the task. Other harnesses reject explicit native budget options instead of silently ignoring them.

Quoted TOML keys are `native_max_turns="60"` and `native_timeout="8m"`, and `native_tool_timeout="180s"`; environment variables are `PROSE_NATIVE_MAX_TURNS` `PROSE_NATIVE_TIMEOUT`, and `PROSE_NATIVE_TOOL_TIMEOUT`. Normal flag > environment > project > user precedence applies. Zero, negative, malformed, infinite or overflowing values are rejected. Omitted values preserve the existing native launch defaults.

SDK invocation, dry-run and result records expose `nativeLimits`: `maxTurns`, `timeoutSeconds`, `toolTimeoutSeconds` (default 30), and `maxOutputTokens` (12000 per model request). The generic SDK harness also reports its configured limits in start and error records. These are configured allowances, not measured consumption. The harness's direct `--max-turns`, `--timeout`, and `--tool-timeout` arguments remain available without either outer runner.

Normalized native failures carry a closed `nativeFailure.kind`: `max-turns`, `timeout`, or `execution`, with validated numeric limits and elapsed seconds when supplied. Arbitrary exception names and bodies are not passed through. A shell-action timeout is a tool result the agent may handle; an SDK run timeout terminates that run; outer cancellation has its existing separate error code. Cleanup signals do not replace the recorded native cause. A file written before a turn limit is reached remains an artifact, not proof of a completed invocation. Success still requires a native final record and successful process settlement.

Increasing the tool allowance does not increase the SDK parent or outer deadline. Omission forwards no extra tool-timeout argument; explicit `30s` reports its selected configuration source while preserving the same effective allowance. A tool timeout can return a tool error for the agent to handle, not guaranteed cancellation of every external effect. No delegation facility or OpenProse-specific behavior is added.
