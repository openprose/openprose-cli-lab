# OpenProse CLI lab

Two independent outer runners, Rust and Bun (packaged through npm), connect an opaque Markdown-owned image and task to an existing agent harness. They do not interpret Contracts or implement the OpenProse language. Keep the interpreter, standard library, and component definitions in the separate Markdown library.

## Choose the image and output contract

Unconfigured builds embed `echo-v0`, a deliberately nonsemantic transport fixture. Running that image does not execute the language. To run a language directory, build with a verified image whose entry directs the agent to its interpreter and requested program. Image source, bundle, and checksum are build inputs; changing them requires no CLI source edit. See [image bundle configuration](cli/shared/image/bundle/README.md).

Both runners support:

- `--output-contract image-envelope` (default): native completion plus the image-declared model-authored terminal envelope.
- `--output-contract native`: actual native completion and final text, without requiring or synthesizing a terminal JSON envelope. Semantic status remains `not-applicable`; evaluate the program's artifacts separately.
- `--output human|json|jsonl`: rendering, independent of those completion rules.
- `--native-log /absolute/new/file.jsonl`: optional private, bounded native-event capture for that same run. It does not prove fulfillment; see [capture limits](docs/native-capture.md).

For example, **after building with a language entry image** and supplying the provider credential in the process environment:

```sh
/path/to/prose --harness claude --auth-profile anthropic-api-key \
  --model haiku --permission-mode acceptEdits \
  --cwd /absolute/language-workspace --output-contract native \
  --output jsonl --native-log /absolute/new-run/native.jsonl run program.md
```

The native-log parent directory must already exist. The image determines how it loads the requested program. Default harness selection is still `openprose`, which reports `HOSTED_UNAVAILABLE`; select an installed harness explicitly. No fallback occurs.

## Harnesses and environment profiles

| Harness | Current routes and capabilities |
|---|---|
| Claude | Installed login or explicit `anthropic-api-key`. The API profile adds native `--bare`, which in 2.1.243 restricts tools to Bash/Edit/Read even with an Agent tool request. Native `acceptEdits` is an explicit permission choice, not implied by API auth. |
| Codex | Installed login or `openai-api-key` through a native custom Responses provider. Explicit `workspace-write` or `read-only` maps to native sandbox selection. Context/skill discovery is a separate concern. |
| Prime / OMP | Explicit `provider/model` and credential profile. Provider-key routes use fresh private configuration; separate harness-login routes preserve native stores. Native tool use is supported. OMP validates its discovered tool inventory rather than requiring it empty. |
| Agents SDK | Optional generic `prose-agents-sdk` executable, explicit model and `openai-api-key`, ordinary shell tool, no built-in delegation or cached-login route. See [installation and limits](docs/agents-sdk-adapter.md). |

Exact admitted versions and platforms are checked at readiness; see the product docs and `cli harness list`. Selected auth is not proof of successful authentication, billing identity, or sufficient capabilities. See [credential routes](docs/api-credentials.md), [permissions](docs/permissions-and-native-notices.md), and [isolated evaluation](docs/isolated-evaluation.md).

Full native Claude delegation was evaluated through an explicitly recorded, opt-in launcher using nonbare mode, fresh configuration, explicit ordinary tools and directory access. It is **not** a stock CLI profile today. The API profile's bare/tool coupling remains an environment limitation; do not describe a missing reviewer as a language failure or invent one. Native task progress and completion are transported without interpreting their purpose.

## Development and provenance

[Build Rust](cli/rust/README.md) · [Build Bun](cli/bun/README.md) · [Native output semantics](docs/native-output.md) · [Native task events](docs/native-task-events.md)

The initial import preserved the predecessor's current dirty `cli/` tree. `provenance/import.json` records imported hashes and predecessor HEAD; `provenance/source-status.txt` records the working-tree state. This is not a claim that the import equals that commit. Private development binaries, runtime compatibility evidence, and language conformance are separate artifacts; no 1.0 or public-release claim follows from transport success.
