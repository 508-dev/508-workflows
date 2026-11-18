# Discord mention routing against chat baselines

This [snapshot](observed.json) records the November 19, 2026 JST comparison
using harness revision
[`be0975f`](https://github.com/508-dev/508-workflows/commit/be0975f37e8af19bd44fed111ef9bc745c3e4b62).
Each candidate received one request per case on the same
[32 synthetic mention inputs](../../routing/mentions.v1.json). The run
repeated Jev alongside the new baselines. The follow-up GPT-4.1 mini sweep
used direct OpenAI and harness revision
[`fd5e691`](https://github.com/508-dev/508-workflows/commit/fd5e6917380bbc05c467f404006ba55d41017df1).
The earlier Clef results are in the
[decision-model snapshot](../mention-routing-2026-11-19/README.md).

| Candidate | Successful calls | Correct routes | New regressions | Workflow diversions | p50 / p95 API latency | Total USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Current rules | — | 27/32 | — | 1 | — | — |
| Jev 1.14 | 32/32 | 32/32 | 0 | 0 | 348 / 432 ms | $0.000593334 |
| Kimi K2.6, non-thinking | 32/32 | 29/32 | 3 | 2 | 605 / 1,128 ms | $0.002853894 |
| GPT-4.1 mini, direct | 32/32 | 26/32 | 5 | 5 | 590 / 678 ms | $0.003066 estimated |

The original GPT-4.1 mini attempts through OpenRouter received HTTP 403 and
remain in the snapshot as failed requests. The table uses the follow-up direct
OpenAI sweep, which resolved all 32 calls to `gpt-4.1-mini-2025-04-14`. That
sweep used 6,917 input tokens, zero cached input tokens, and 187 output
tokens. The cost estimate uses $0.40 per million input tokens, $0.10 per
million cached input tokens, and $1.60 per million output tokens. Jev and Kimi
costs come from OpenRouter usage. API latency includes the path through the
respective providers. [OpenAI rate card](https://developers.openai.com/api/docs/models/gpt-4.1-mini)

Jev corrected five misses in the rules and reproduced its earlier 32/32 result.
Kimi corrected the same five misses, misrouted a live job search to knowledge,
and sent two workflow requests to job matching. Jev used about one fifth of
Kimi's reported cost and had 2.6 times lower p95 API latency on this sweep.
The two model families use the same routing criteria. Jev defers below 0.85
or on abstention, while chat defers on an explicit abstention. Kimi deferred
once, and Jev deferred nine times. GPT-4.1 mini deferred once, corrected
four misses in the rules, and introduced five regressions. It diverted four
previously correct workflow requests to job matching and missed the same
loose CRM lookup as the rules.

Discord dispatch currently uses rules. The backend resolves Kimi K2.6 via
Bifrost or Fireworks by default when configured, with GPT-4.1 mini as the
built-in fallback. The deployed overrides were unavailable for verification.
The Kimi calls here used OpenRouter, which served the selected requests
through Baidu, CoreWeave, and Crusoe. These measurements cover label selection
on a single synthetic corpus. Production planner drafting and end-to-end
latency need separate measurements.

See the [eval harness](../../../../../docs/discord-agent-eval-harness.md) to
repeat the comparison. Verify this snapshot from the repository root with:

```bash
sha256sum -c tests/evals/discord-agent/artifacts/mention-chat-baselines-2026-11-19/SHA256SUMS
```
