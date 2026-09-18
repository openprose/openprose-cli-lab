# Recover an interrupted action

The loop saves an attempt before starting work. If an action fails, times out or is interrupted, it can leave real effects behind. The checkpoint keeps that attempt pending. Restarting reports `recovery-needed` and does not repeat it.

Recovery records an operator's investigation. It does not undo an effect, certify contract compliance or reset the action budget. The same procedure works with either sidecar, including a checkpoint written by the other runtime.

## Inspect before settling

1. Stop the serving owner and establish that the action and its descendants have stopped. A timeout or a missing process ID alone is not proof that external work stopped.
2. Run `status CONFIG` and retain the checkpoint's exact `binding` and `pending` values. Review the action receipt, affected files and any external service involved. The checkpoint describes intent, not proof of what happened.
3. Determine whether the action completed or was not applied. If effects are partial or uncertain, reconcile them outside the loop before choosing an outcome. Leave the attempt pending until that investigation is conclusive.
4. Write a durable investigation record and choose a non-secret reference to it. The receipt argument stores that reference in the checkpoint; the sidecar does not fetch or verify the referenced record.

An existing `service.lock` or `lock` blocks settlement. The CLI never removes these automatically and offers no force-unlock command. After abrupt termination, a trusted operator must establish exclusive ownership and resolve any uncertain checkpoint save before removing a stale lock. Do not delete the checkpoint to get past recovery.

## Record the outcome

For the compiled private sidecars, replace the placeholders with the exact values from the inspected checkpoint:

```sh
/absolute/bundle/bin/weave-bun settle /absolute/config.json \
  --binding 'EXACT_CHECKPOINT_BINDING' \
  --attempt 'EXACT_PENDING_ATTEMPT' \
  --outcome completed \
  --receipt 'local-record:review-001'
```

Use `weave-rust` in place of `weave-bun` for the native Rust sidecar. From source, use `bun --no-env-file experiments/weave-seed/local/run.mjs` in place of the binary. Options must appear in the order shown. The other valid outcome is `not-applied`; use it only after verifying the action produced no effects, or after reconciling those effects to that state.

A successful command prints a JSON record:

```json
{"status":"settled","attempts":1,"pending":null}
```

The attempt count remains unchanged. Settlement sets the previous judgment to `unknown` and expires it, so the next step must obtain and assess current evidence. `completed` describes the action's outcome; it does not mean the contract is satisfied. `not-applied` does not refund the attempt or guarantee a further action: the next assessment and remaining budget decide that.

Settlement requires the configuration's checkpoint location, matching binding and attempt, a valid outcome and a nonempty receipt. It does not need provider keys, readable evidence or runnable provider capabilities, and makes no provider or action call. An incorrect binding, attempt, outcome or receipt fails without replacing the checkpoint. A second settlement of an already resolved attempt also fails.

## Resume explicitly

Restore any missing input or configuration, run `check CONFIG`, then invoke `step CONFIG` or a bounded `serve CONFIG` when ready. The usual provider charges and effect permissions apply to this new execution. Retain the investigation record alongside any private action receipts.

This workflow is for cooperating processes in a trusted local filesystem. It is not a distributed recovery protocol or an independent audit of external effects.
