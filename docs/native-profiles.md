# Explicit native context and tools

Both runners accept `--native-profile claude-workspace-tools` for admitted Claude 2.1.243. This exposes the native Read, Write, Edit, Glob, Grep, Agent, and Bash tools under safe mode with user/project/local setting sources disabled. It does not add tool permission grants. The native inventory may call delegation `Task` even though its tool calls use `Agent`.

An explicit example, for a trusted synthetic workspace:

```sh
prose --harness claude --auth-profile anthropic-api-key \
  --native-profile claude-workspace-tools \
  --permission-mode acceptEdits \
  --native-allow-tool Agent --native-allow-tool Read \
  --native-allow-tool Write --native-allow-tool 'Bash(cat *)' \
  --native-add-dir /absolute/additional-inputs \
  --output-contract native --model sonnet run task.md
```

The image remains a separate build input. This command does not change what a compiled image asks the model to do. Native output mode accepts native terminal settlement without requiring a model-authored result envelope; it does not certify task fulfillment.

`--native-add-dir PATH` repeats to extend native directory access; relative paths resolve against invocation cwd and must name existing directories. No parent directory is implicitly added. `--native-allow-tool RULE` repeats to forward an explicit native permission rule through `--allowedTools`. Rules retain native syntax. The profile itself never enables bypass or supplies permission grants. Existing `--permission-mode` remains separate. Native managed policy may restrict available tools or permissions further; these options are not OS confinement.

Omitting the profile, or selecting `default`, preserves previous behavior. In particular the default API-key Claude route remains bare with its smaller native tool inventory. Additional directories and permission-rule lists require `claude-workspace-tools`; using these options with another harness fails instead of being ignored.

Configuration accepts `native_profile`, `native_add_dirs = ["path"]`, and `native_allow_tools = ["Agent", "Read"]`. List values are single-line arrays of quoted strings. Flags replace the corresponding entire configured list; repeated flags accumulate within the flag list. `PROSE_NATIVE_PROFILE` selects the profile through normal configuration precedence. There are no list-valued environment variables.

## Authentication is separate

`anthropic-api-key` still requires ANTHROPIC_API_KEY and strips competing credential variables. In this nonbare profile the runner supplies a fresh private native configuration directory and verifies that native init reports ANTHROPIC_API_KEY as its credential source. A missing or contradictory source fails the run. It never retries using subscription credentials. Unlike the default bare route, this profile does not claim that native code never consults a keychain; reported source is an observation, not proof of the billed account.

`claude-subscription` retains the native authentication store while selecting the same safe-mode context and seven-tool set. The profile never selects an account by itself.

Dry-run, invocation, and result metadata include `nativeConfiguration` when this profile is selected. Requested tools, directory access, permission rules, credential selection, and config ownership are distinct from the `observed` native tool inventory and credential source. Dry-run observations are null. Existing opt-in `--native-log` captures the actual records; its redaction limits still apply. A requested tool list does not prove that the environment exposed every requested tool.
