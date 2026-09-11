# Decision 0003: preserve attempted-process failure evidence

Status: accepted

A failed installed-process attempt remains attributed to the adapter and
transport that actually ran. It does not collapse into an unavailable or
generic adapter result.

For the shared fake-process oracle this means retaining:

- adapter `mock/fake-process` and its frozen descriptor/capabilities;
- delivered-image evidence once spawn/delivery occurred;
- native exit and canonical signal independently;
- whether a terminal event was structurally observed; and
- a terminal-envelope digest when one was structurally recovered.

Protocol and process failures report semantic status `unknown`, even if a
terminal envelope was observed, because the complete transport contract did
not settle successfully. `terminal.transportCompleted` is true only when the
required terminal event and acceptable native exit both hold.

The shared process/protocol error detail object is exactly:

```json
{
  "processExit": null,
  "processSignal": "SIGTERM",
  "terminalEventObserved": false
}
```

`processExit` is an integer or null; `processSignal` is the canonical signal
name or null. Product-specific reason strings are not part of stable machine
output. Cancellation uses terminal classification `cancelled`, keeps native
exit/signal separate, and mirrors those three evidence fields.
