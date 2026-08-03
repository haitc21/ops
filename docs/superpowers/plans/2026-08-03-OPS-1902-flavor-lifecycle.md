# OPS-1902 Flavor lifecycle

- [x] RED: add provider-handler and production-registry tests; observe unsupported flavor dispatch.
- [x] GREEN: add typed pinned contract, command routing allow-list, registry bindings, and Nova `compute` create/delete/access/spec convergence.
- [x] Refactor: isolate flavor behavior in the existing resource-operation handler and format/type-check it.
- [x] Review/security: self-review verifies no delete/recreate update path, exact-shape replay check, idempotent missing delete, and no credential logging.
- [ ] Live: paired CPS curl flow and independent Nova CLI comparisons.
- [ ] Cleanup/runbook: append redacted results and zero-residual confirmation to paired runbook.
- [ ] Commit: task-scoped OPS-1902 commit only after independent review/live evidence.

Blast radius: message delivery allow-list, production registry, generic resource-operation handler, and Nova compute proxy. Contract version stays `1.0`.
