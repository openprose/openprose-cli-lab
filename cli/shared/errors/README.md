# Stable runner errors

`taxonomy.v1.json` freezes the v1 code, boundary, portable exit code, default
message, and single corrective action shared by both runners. Implementations
may attach sanitized mechanical details, but they do not paraphrase the
corrective action in machine output.

Messages name the failed boundary. Actions are safe to show to agents and
people and never contain discovered secrets. A language semantic failure is
not a runner error and therefore is not listed here; its portable exit code is
30 as defined by the result contract.
