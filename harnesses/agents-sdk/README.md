# Generic OpenAI Agents SDK local harness

A normal `Agent` + `Runner` loop with one general shell tool. The model chooses every tool action. This harness treats instruction-file contents as opaque text and has no knowledge of Contracts, references, programs, acceptance, state, or libraries. It is an optional bring-your-own-harness test implementation, not an OpenProse dependency.

## Install and run

Use Python 3.10 or later:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py --model MODEL --cwd /path/to/workspace --prompt 'Read README.md and perform the requested task.'
```

Supply `OPENAI_API_KEY` in the process environment or pass `--env-file /private/path/.env`. Only OPENAI_API_KEY is loaded from that file. An optional `--instructions FILE` appends opaque instructions to a generic coding-agent instruction. The working directory is identified in instructions. No file is automatically discovered or interpreted.

JSON lines on stdout record starts, actual tool commands and results, final text and token usage, or error type. Exit 0 means Runner returned a final response, **not that the requested task passed acceptance**. Observers must inspect results independently. Tracing export is disabled. Exceptions expose types rather than potentially sensitive request bodies.

Defaults: 180 second overall timeout, 30 seconds per shell action, 20 Runner turns, 12,000 output tokens per model request. Shell timeouts and cancellation terminate the shell process group. Tool stdout/stderr are each truncated to 30,000 characters. No hidden retries, output repair, adjudication, or program-specific policy is supplied by this wrapper; SDK/provider transport behavior remains upstream behavior.

## Environment boundary

The shell is ordinary local bash with a supplied working directory. **This is not an OS sandbox:** use a separately isolated machine/container for untrusted programs. Keys/tokens/secrets/password variables are excluded from the shell environment, but the shell can still read host files within OS permissions. This wrapper adds no filesystem confinement. Tests use synthetic data in separate copied workspaces.

No web, subagent, or notification tool is provided in this initial profile. Unsupported program requirements must be handled by the model rather than silently provided by an evaluator.

## Validation

```sh
.venv/bin/python test_run.py
```

Two local tests exercise actual read/write/exit behavior and process-group timeout cleanup. Initial real-model observations are in `EVIDENCE.md`. No language acceptance tests are embedded in the harness.

Implementation followed the official [Agents SDK migration example](https://developers.openai.com/cookbook/examples/agents_sdk/migrate-from-claude-agent-sdk/readme) and inspected installed SDK signatures. Pinned direct dependencies: OpenAI Agents SDK 0.22.2, OpenAI Python 3.13.0, python-dotenv 1.2.3.
