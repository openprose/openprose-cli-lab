# Independent fake harness

This standard-library-only executable is the black-box process boundary used
by both runner implementations. It is not an adapter implementation and never
acts as an OpenProse semantic fallback.

```sh
python3 cli/conformance/fake-harness/fake_harness.py run \
  --scenario success \
  --image-file /path/to/exact-image-bytes \
  --task-file /path/to/exact-task-json \
  --observation-file /path/to/observation.json
```

The observation records the exact image and task bytes in base64 plus their
digests, the argument vector, cwd, and only three allowlisted runner metadata
environment names. It deliberately does not copy the ambient environment.

Scenarios:

- `success`: structured start, message, and sentinel terminal records;
- `malformed`: invalid JSONL;
- `truncated`: EOF in the middle of a JSON record;
- `eof-without-terminal`: valid records without the required terminal;
- `nonzero`: valid start followed by process exit 17;
- `terminal-nonzero`: nominal terminal followed by process exit 17;
- `delay`: delay, then success;
- `stderr`: a diagnostic on stderr while stdout stays structured;
- `fragmented`: a valid stream written in deliberately fragmented chunks;
- `crlf`: valid structured records with CRLF framing;
- `duplicate-terminal`: two terminal records;
- `reordered`: a message before session start; and
- `descendant`: cancellation-resistant child and grandchild in the inherited
  process group, with no terminal record.

The descendant scenario must be launched in an owned containment boundary by
its caller. `--descendant-pid-file` makes its identities observable for cleanup
audits. The fixture itself does not kill or target unrelated processes.
