# Experimental weave core (IMP-017)

This headless Python reference explores assess/act over caller-bound evidence. It is not a shipped CLI command, Markdown interpreter, or published SDK. The original planning proposal used IMP-016; workspace IMP-017 resolves that ID collision.

`reconcile(binding, checkpoint, observe, assess, act, save, clock, new_id)` accepts ordinary capabilities. Observation reads a declared file or SQLite result; assessment and action are the two intelligent roles. Contract resolution remains external. The binding string must identify the contract, evidence selector, assessment policy and permissions; changing any invalidates reuse.

One call performs at most one action. It records a pending attempt before action, then rereads and reassesses; a normal actor return is not fulfillment. Pending attempts require explicit recovery instead of replay. The host owns scheduling, durable checkpoint storage, serialization and action budgets. `max_attempts` is cumulative for the supplied checkpoint, not automatically replenished after a failure or new event.

SQLite results use multiset semantics: row order is ignored, duplicate rows retained, column names and query/parameters bound into identity. Ordered output needs a different selector. A query result is a projection; an omitted column cannot influence a judgment. File reads are UTF-8 and bounded. Expiry is separate from content identity; an unchanged digest does not establish external-source freshness.

Run `python3 -m unittest discover -s experiments/weave -p 'test_*.py' -v` from the repository root. Cases are specified in CASES.md. Tests use fresh local state and fake assessment/action; they establish no model reliability. The final reread detects tested races, but cannot make a remote world atomic or eliminate a change after return. No exactly-once guarantee is claimed.

Research evidence belongs to openprose-expedition under imp-017-weave; user-authorized campaign ends September 18 at 08:00 Eastern. No release or foundation promotion follows automatically.
