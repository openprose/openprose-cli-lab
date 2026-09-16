# Published kernel startup

Status: IMP-008 candidate, provider-free validation across Bun and Rust. Live provider/model checks and review remain outstanding. This does not qualify a release or establish model conformance.

Ordinary compiled builds resolve `https://pkg.prose.md/kernel.md` before launching an installed harness. They accept only a redirect into the official origin's immutable `/releases/<release>/core/README.md` layout, fetch that release's descriptor and inventory, verify the inventory digest and kernel entry digest, and freeze the verified bytes for that invocation. They do not parse Markdown, install packages, create locks or implement Prose commands.

The trust anchor is the official HTTPS origin; a checksum retrieved from that origin is integrity evidence, not independent authentication. Startup has a separate 15-second total retrieval budget, a 256 KiB metadata limit and a 32 KiB kernel limit. No automatic retry, stale-cache fallback, echo fallback or provider substitution occurs. Rust cancellation is checked between requests; an in-flight blocking request remains bounded by the retrieval deadline. The selected kernel then uses the existing native execution timeout and capture limits.

The verified full kernel is delivered as appended instructions. The canonical task JSON is separate:

| Harness | Instruction mechanism | Credential routes covered by preparation |
|---|---|---|
| Codex | Additional `developer_instructions`; native defaults retained | Existing ChatGPT login, OpenAI API |
| Claude | `--append-system-prompt-file` | Anthropic API; existing subscription route remains supported |
| Prime | `--append-system-prompt` text | Anthropic, OpenAI, OpenRouter |
| OMP | `--append-system-prompt` file | Anthropic, OpenAI, OpenRouter |
| Agents SDK | Instructions file appended to generic harness instructions | OpenAI API |

These channels have native names and are not all a literal wire-level system role. Codex serializes its instruction content as developer messages. None of these startup paths inserts the kernel into the user task. Model identifiers remain user-selected and opaque; successful readiness does not prove model availability, API authentication or identical model behavior. Existing version admission, credential separation, native permissions and platform restrictions remain in force.

## Use

Build either implementation normally, install a supported native harness, and select it explicitly or through the existing saved harness command:

```sh
prose cli harness use codex
prose --model MODEL --permission-mode workspace-write run hello.prose.md
```

Use a model available to your account. Existing ChatGPT login remains Codex's default route. API keys belong in the parent environment with an explicit `--auth-profile`; never in argv or committed files. Prime and OMP require a qualified provider/model and explicit profile. See [credential routes](api-credentials.md).

Ordinary published-kernel builds default to native completion. Artifact fulfillment must still be checked independently. Help, version and local configuration/doctor operations do not retrieve the kernel. Doctor's optional `imageSource: published-on-run` identifies deferred selection; its `image` object describes only the embedded diagnostic fixture, not an executed fallback. Run `--dry-run --output json` to resolve the kernel and check the selected native setup without starting a model.

Default harness selection is unchanged: an unconfigured hosted selection still reports unavailable. No silent harness selection is introduced.

## Fixed inputs and tests

Explicit image builds continue to consume a verified image directory, bundle and checksum; they do not contact the moving kernel entry. They preserve the image-envelope default unless the caller selects `--output-contract native`. Both ordinary compiled implementations append Codex instructions for explicit images too. Bun's earlier `--codex-instructions base|framed` remains an explicit-image experiment option; published startup rejects those modes.

Source-level test seams and dedicated test builds retain hermetic fixture behavior. They do not establish the ordinary compiled default; the shared compiled-process test separately checks Codex append delivery, task separation, model selection, child failure and timeout. Release-image qualification rejects moving published startup; select an explicitly qualified image for that gate. This candidate does not promote a published artifact.

Runtime project-kernel lock selection is not implemented here. A fixed image build is the current deterministic pinning route. Initialization and package-lock semantics belong to the separately planned command interface. No company lock or installed dependency is changed by startup.

## Verification and limitations

The two implementations use the same acquisition policy, image template and shared HTTP fixtures. Tests cover exact bytes and digests, origin/path rejection, malformed or modified metadata/content, size bounds, task separation and native argv limits. The selected release and content identity appear in ordinary image/result metadata. The public endpoint was also checked through dry runs of both compiled implementations, without a model.

A 20-route local readiness matrix covers both runners and the ten credential routes above (two Codex, one Claude, one SDK, three Prime, three OMP). Synthetic API keys test selection only; `ready` must not be read as authenticated. Existing platform/version restrictions remain; these checks were on macOS ARM64. Stronger ambient context control belongs to IMP-009. Live provider/model calls remain deferred until credentials and bounded attempt allocations are supplied.

**Task — [IMP-008](https://github.com/openprose/openprose-workspace/blob/main/work/items/IMP-008.md):** authorization, branches and evidence.
