# Install a private review bundle offline

This installer places one reviewed bundle in a stable private directory. It does not publish a package, install globally, change PATH or shell profiles, select a provider, initialize a framework program, migrate state, or start any executable. Current binary admission is macOS arm64 only. A successful install verifies files; it does not verify provider access, semantic correctness or a running service.

Obtain the bundle's exact `manifest.json` SHA-256 through a trusted review channel. The unsigned manifest is not independent publisher authentication, and calculating its digest from an untrusted download does not establish trust. Use the supplied trusted digest explicitly:

```sh
python3 -B /absolute/reviewed-source/experiments/weave-seed/distribution/install.py \
  --bundle /absolute/reviewed-bundle \
  --manifest-sha256 FULL_64_CHARACTER_LOWERCASE_TRUSTED_DIGEST \
  --destination /absolute/new-private-installation
```

The destination must be fresh and its parent must exist. Existing files, directories and symlinks are never replaced. The source and destination cannot contain one another. The installer itself must come from a trusted reviewed source; validating a bundle does not authenticate an arbitrary installer used to read it.

The result contains:

- `payload/`: the unchanged manifest and exact inventoried bundle bytes, including standalone sidecars and source helpers.
- `installation.json`: a private installation receipt outside the bundle inventory, containing the reviewed manifest hash and installed paths.

All directories are mode 0700; files are mode 0600 except the two inventoried sidecar executables, which are mode 0700. Receipt and stdout expose paths and digests, never credential values. `providerVerified` and `executablesLaunched` are false. No executable permission is granted to arbitrary source files merely because they had it in the incoming bundle.

## Verification and failure behavior

Before copying, the installer checks the supplied manifest digest, known schema, private publication status, supported platform, exact required bundle entries and bounded inventory metadata. It rejects ambiguous/traversing paths, duplicate manifest keys, symlinks, nonregular files, missing/extra files and unlisted directories. Each regular file is read and hashed through a bounded descriptor. Limits are 4 MiB of manifest JSON, 4096 inventoried files, 128 MiB per file and 256 MiB total payload.

The installer copies only declared bytes, then verifies the complete source and destination inventories again. The installation receipt is written last, after verification. No shell, native binary, build tool, credential store or network client is invoked. The copied installer uses only Python's standard library.

A failed copy leaves its newly claimed private directory for inspection, without a successful acceptance response. A missing, partial or invalid receipt means incomplete installation. Do not run partially copied binaries. Inspect the failed directory and use a fresh destination when retrying. Existing installations are untouched. File fsync does not establish universal power-loss durability, and these checks are not a defense against hostile concurrent filesystem replacement. Verify again after a crash or suspected modification.

## First run from the installed copy

Use the absolute paths printed in the successful JSON record. For example:

```sh
/absolute/new-private-installation/payload/bin/weave-bun --help
/absolute/bun --no-env-file \
  /absolute/new-private-installation/payload/source/experiments/weave-seed/getting-started/create.mjs \
  /absolute/new-subject
/absolute/new-private-installation/payload/bin/weave-bun check /absolute/new-subject/config.json
/absolute/new-private-installation/payload/bin/weave-bun step /absolute/new-subject/config.json
/absolute/new-private-installation/payload/bin/weave-rust step /absolute/new-subject/config.json
```

The synthetic example needs an installed Bun for its fixture processes, while the compiled coordinators need no separate Bun process for their own commands. Generate configuration **after** installing so its capability paths refer to the stable installed copy. The first fixture step repairs once and the next fresh step reuses across runtimes. These are offline mechanical checks. The [BYOK guide](../getting-started/BYOK.md) explains the additional explicitly installed CLI/harness, selected kernel, credentials and budget required for real provider work.

For commands in copied source guides, use `payload/source` as the repository root. Keep subject files, checkpoint directories and private receipts outside the installation payload; changing the payload invalidates its manifest inventory. PATH remains unchanged; call the absolute paths or manage your own reviewed launcher separately.

## Use an explicitly selected CLI bridge

Newer receipts include `helpers["host-binding.mjs"]` when that helper is present in the verified bundle inventory. Older bundles still install and report their original helpers; this installer does not add a bridge helper to them. The native `prose` CLI is a separate explicit installation. Select a reviewed CLI that supports `cli weave`; the helper does not probe its version or run it.

After creating the synthetic subject above, create a fresh binding outside the installation payload:

```sh
/absolute/bun --no-env-file \
  /absolute/new-private-installation/payload/source/experiments/weave-seed/getting-started/host-binding.mjs \
  --host /absolute/new-private-installation/payload/bin/weave-bun \
  --config /absolute/new-subject/config.json \
  --output /absolute/new-subject/host-binding.json \
  --environment-keys '[]' \
  --timeout-ms 300000 \
  --max-output-bytes 1048576 \
  --prose /absolute/reviewed-prose
/absolute/reviewed-prose cli weave \
  --host-binding /absolute/new-subject/host-binding.json \
  check /absolute/new-subject/config.json
```

The helper hashes the selected installed coordinator and prints check, status, step and finite serve commands. It creates the binding without starting the CLI, coordinator or provider. The empty environment selection is for the offline synthetic example. For BYOK work, review the required environment names and budgets in the [setup guide](../getting-started/BYOK.md). Follow the [host-binding walkthrough](../getting-started/README.md) for the bridge flow. A successful bridge check reports configuration diagnostics; it does not establish provider access or semantic correctness.

## Side-by-side upgrades and removal

Install a newly reviewed bundle with its own trusted digest into another fresh directory. The installer has no mutable `current` alias and never rewrites an existing subject's argv, source bindings, checkpoints, pending effects or attempt count. Old configurations keep referencing the old installation. This avoids silently changing capabilities during an active invocation.

To adopt a new installation, first stop the relevant local service, inspect its current status and reconcile any uncertain effects using the documented recovery process. Review the new capability identity and edit only the intended configuration while preserving its existing checkpoint directory and cumulative budget. A new directory is not permission to reset attempts, discard pending work or exceed the existing spending authorization. Run offline check again before an explicitly authorized step. Compatibility and migration are separate review decisions, not consequences of copying files.

There is no automatic uninstaller. Remove an old installation only after confirming no configuration or running process still depends on it. Keep subject/checkpoint/evidence directories separately and preserve them as required. This guide does not authorize their deletion.

## Offline acceptance checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s experiments/weave-seed/distribution -p test_install.py -v
```

Eight self-contained tests cover exact independent copies and private modes, side-by-side state preservation, manifest/platform/path failures, corruption/missing/extra files, symlinks/FIFOs, existing destinations, interrupted copies, source mutation during copying, a standalone copied installer's help, and optional verified bridge-helper paths for old and new bundles without executing them. They do not launch an installed fixture or assert compatibility of real binaries. A separate installed-artifact journey must run from the installed copy with the source bundle unavailable before reporting that qualification.
