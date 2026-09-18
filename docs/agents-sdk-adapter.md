# Agents SDK adapter

Both runners accept `--harness agents-sdk --model MODEL --auth-profile openai-api-key --output-contract native`. They discover an executable named `prose-agents-sdk` on the explicitly selected PATH. This optional Python harness receives opaque instructions, a task prompt and a working directory. It exposes an ordinary shell tool; neither the adapter nor harness interprets Contracts or OpenProse syntax.

## Provision a private launcher

Use a reviewed **full CLI source checkout** containing `harnesses/agents-sdk/run.py` and `requirements.txt`. A private weave review bundle does not include those installation sources. Record the selected checkout revision and file hashes. Current native admission is `prose-agents-sdk 0.1.0` on macOS arm64; other environments need separate qualification.

Choose an installed Python 3.10 or later and fresh private directories. Replace each absolute placeholder below. The virtual-environment path must contain no whitespace and must be short enough for a direct shebang; the launcher generator checks this conservatively. Do not resolve its `bin/python` symlink to the system interpreter: the virtual-environment path is needed to load the installed dependencies.

```sh
PROSE_CLI_SOURCE=/absolute/reviewed/prose-cli
PROSE_PYTHON=/absolute/python3
PROSE_SDK_VENV=/absolute/new-private-venv
PROSE_HARNESS_BIN=/absolute/new-private-harness-bin
```

The next commands create the environment and install dependencies. Dependency installation may access your configured package index; run it only as an explicit provisioning action. They do not invoke a model. The requirements pin direct dependencies, not the entire transitive graph; retain the resolved package versions for your installation.

```sh
umask 077
test ! -e "$PROSE_SDK_VENV" && test ! -L "$PROSE_SDK_VENV" && \
  "$PROSE_PYTHON" -m venv "$PROSE_SDK_VENV" && \
"$PROSE_SDK_VENV/bin/python" -m pip install \
  --requirement "$PROSE_CLI_SOURCE/harnesses/agents-sdk/requirements.txt" && \
"$PROSE_SDK_VENV/bin/python" -m pip freeze
```

Run each step only after the preceding step succeeds. The requirements currently select `openai-agents==0.22.2`, `openai==3.13.0` and `python-dotenv==1.2.3`. For offline provisioning, use separately reviewed cached packages; this guide does not download or manage a cache automatically.

Create the launcher with the virtual environment's absolute interpreter. The generator copies the reviewed harness bytes, changing only its first shebang line. It refuses an existing launcher directory and never executes the harness. Its parent directory must already exist. A partial directory after an error is not an accepted installation; inspect it and choose a fresh destination.

```sh
"$PROSE_SDK_VENV/bin/python" - "$PROSE_CLI_SOURCE" "$PROSE_SDK_VENV/bin/python" "$PROSE_HARNESS_BIN" <<'PY'
import hashlib, os, pathlib, sys
source_root, interpreter, destination = sys.argv[1:]
source = pathlib.Path(source_root) / 'harnesses/agents-sdk/run.py'
folder = pathlib.Path(destination)
if not all(pathlib.Path(p).is_absolute() for p in (source_root, interpreter, destination)):
    raise SystemExit('Use absolute source, interpreter and launcher paths.')
if any(c.isspace() for c in interpreter) or '\0' in interpreter or not os.access(interpreter, os.X_OK):
    raise SystemExit('Use an executable virtual-environment interpreter without whitespace.')
shebang = ('#!' + interpreter + '\n').encode('utf-8')
if len(shebang) > 127:
    raise SystemExit('Use a shorter virtual-environment interpreter path.')
original = source.read_bytes()
first, separator, body = original.partition(b'\n')
if first != b'#!/usr/bin/env python3' or not separator:
    raise SystemExit('Review the changed source launcher format before proceeding.')
folder.mkdir(mode=0o700)
launcher = folder / 'prose-agents-sdk'
data = shebang + body
fd = os.open(launcher, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
with os.fdopen(fd, 'wb') as stream:
    stream.write(data)
    stream.flush()
    os.fsync(stream.fileno())
print('launcher:', launcher)
print('source-sha256:', hashlib.sha256(original).hexdigest())
print('launcher-sha256:', hashlib.sha256(data).hexdigest())
PY
```

Keep that virtual environment and launcher at their selected paths. Moving or changing either requires a reviewed replacement; the native CLI executable digest does not attest all harness dependencies. Nothing above edits a PATH profile or installs globally.

## Verify locally before a provider run

These two checks use an empty environment and do not invoke a model or load credentials:

```sh
env -i "$PROSE_HARNESS_BIN/prose-agents-sdk" --version
env -i "$PROSE_HARNESS_BIN/prose-agents-sdk" --help
```

Version output must be `prose-agents-sdk 0.1.0`. Help must show the model, cwd, prompt and native budget options. Successful import/help does not prove provider access or model availability.

For native CLI readiness, select the launcher directory explicitly in the process PATH; for example, `PATH="$PROSE_HARNESS_BIN:/usr/bin:/bin"`. Supply `OPENAI_API_KEY` through your trusted process environment, never a source file or copied support report. Use the selected CLI's provider-free doctor and dry-run routes with the same model, cwd and bounds planned for the action; see the [native actor setup](../experiments/weave-seed/integration/native-actor/README.md#explicit-setup). The weave actor requires a matching **fixed-image CLI with test seams disabled**, not an ordinary published-on-run build. Follow [fixed-image staging](../experiments/weave-seed/getting-started/FIXED-IMAGE.md) before configuring that actor.

When using the weave bridge, its host binding, the coordinator and the actor each filter environment names. Admit PATH and OPENAI_API_KEY at every required layer, and admit the separate assessor key in the bridge/coordinator if selected. A successful offline `check` does not execute these native readiness checks or establish provider authentication. The ordinary CLI defaults to the unavailable hosted route, so keep `--harness agents-sdk` and the API-key profile explicit.

## Effects, limits and evidence

There is no cached-login route in this harness. The shell receives a scrubbed environment without credential-like variables, but it can read host files allowed by the operating system: this is not filesystem confinement or an OS sandbox. Native events are start, tool_call, tool_result, final or error. Only final settles a successful native run; final text and exit zero do not establish program fulfillment.

Use the [SDK execution budgets](sdk-budgets.md) for the separate inner and outer deadlines. Turn and token limits are not an aggregate dollar cap. A model may make prohibited operations through its available shell; artifact-only assessment cannot certify procedural compliance.

The launcher recipe was checked with an already installed Python environment and dependencies on macOS arm64: exclusive creation, exact copied body, private executable permissions, and empty-environment help/version. No package installation, network or provider request was used for that check. It does not qualify a fresh dependency install or a complete live BYOK run. Earlier development canaries exercised native reads and writes; their evidence does not generalize to every program or prove language conformance.
