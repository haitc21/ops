# OPS AI-Assisted Delivery Workflow

## 1. Frame one provider behavior

Start from an active OPS story and paired CPS contract. State the provider action, capability/precondition, expected terminal state, timeout, retry classification, normalized result/error, and explicit non-goals.

```text
Story: OPS-xxx
Paired CPS story/schema:
Provider behavior:
Capability/precondition:
Terminal state:
Replay strategy:
Verification:
```

## 2. Discover efficiently

Use CodeGraph first from the CMP workspace root for symbols and call paths. Use RTK-prefixed commands for exact searches, Git, tests, and Docker. Inspect the relevant OpenStackSDK proxy/resource implementation and tests in `opensource/openstacksdk` before designing a wrapper. Do not rely only on README examples.

Discovery must answer:

- Which supported SDK API performs the behavior?
- What service/version/capability is required?
- What exceptions and provider states occur?
- What happens if the command is duplicated after provider success?
- Which data is secret or unsafe to publish?

## 3. Pin contract and build mapper first

Validate the CPS schema/checksum, add a failing golden mapper test, and keep SDK objects inside the adapter. Mapper output must be plain validated common-contract data.

## 4. Build handler test-first

Test in this order:

1. capability and request validation;
2. successful SDK call and mapping;
3. invalid state/not-found/auth/quota/conflict;
4. transient retry and deadline;
5. provider success followed by result-publish failure/redelivery;
6. graceful shutdown/in-flight handling;
7. RabbitMQ integration;
8. real OpenStack acceptance.

Use deterministic fake clock/sleeper for waiter/retry tests.

## 5. Failure and replay design

Before mutation, determine how redelivery recognizes existing success. Create uses operation metadata/marker and provider search. Power actions inspect current state. Delete treats already absent as idempotent tombstone success. Never depend on OPS local persistence.

## 6. Verify on real OpenStack safely

- Discover capabilities first.
- Use uniquely named/tagged test resources.
- Record resource IDs and cleanup plan without secrets.
- Run the narrow scenario, then verify inventory/operation convergence through CPS.
- Confirm cleanup, including retained/deleted root volumes according to policy.

## 7. Completion report

Report story ID, schema checksum, SDK APIs used, capabilities observed, tests/results, replay/timeout evidence, redaction evidence, real resource cleanup, known limitations, and commit.

## Vibe-coding guardrails

- Never generate one giant “OpenStack client” module.
- Do not mirror every SDK field; map only contract/common/provider attributes with current use.
- Do not confuse a mocked success with cross-service completion.
- Do not add a database to solve replay; use durable CPS state and provider observation.
- Stop and split when one change spans multiple OpenStack services without a single acceptance outcome.
