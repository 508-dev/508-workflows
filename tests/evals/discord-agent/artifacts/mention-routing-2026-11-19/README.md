# Discord mention routing live comparison

The [observations](observed.json) record the November 19, 2026 JST comparison
on revision [`ef34f73`](https://github.com/508-dev/508-workflows/commit/ef34f73e21f1f9dbe1d05f1d0273065f49231b09).
Each candidate received one request per case from the
[32-case synthetic mention corpus](../../routing/mentions.v1.json) through the
OpenRouter Decisions API. OpenRouter selected the serving providers. The
snapshot retains the returned model and provider, the choice probabilities,
fallback and effective routes, elapsed request time, and reported usage. It
contains no Discord traffic or request headers.

| Candidate | Correct | Improvements | Regressions | Workflow diversions | Deferred | p50 / p95 API latency | Reported cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Rules | 27/32 | — | — | 1 | — | — | — |
| Jev 1.14 | 32/32 | 5 | 0 | 0 | 9 | 358 / 442 ms | $0.000593334 |
| Clef | 30/32 | 4 | 1 | 2 | 12 | 638 / 1,270 ms | $0.001818912 |
| Clef Flash | 28/32 | 4 | 3 | 4 | 9 | 640 / 941 ms | $0.000228285 |

All 96 requests succeeded. The effective route uses the rules when a model
abstains or its winning probability is below 0.85. Workflow diversions include
the cases inherited from the rules after fallback. Latency measures the API
call and response processing, without Discord or backend handler time.

Jev is the candidate to carry forward. At 0.90 it would defer on the loose CRM
lookup and inherit the rules' workflow diversion. This is one pass over
synthetic cases, including cases used to develop the rules. Measure performance
on held-out, sanitized Discord turns before enabling a model in production.

Use the [eval harness instructions](../../../../../docs/discord-agent-eval-harness.md)
to repeat the comparison. Verify this snapshot from the repository root with:

```bash
sha256sum -c tests/evals/discord-agent/artifacts/mention-routing-2026-11-19/SHA256SUMS
```
