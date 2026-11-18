# Job lead employment classification benchmark

The November 19, 2026 comparison tested employment classification on 26
hand-labeled English and Japanese snippets. Jev alone scored 25/26. The
Jev-first worker policy with GPT-4.1 mini fallback scored 22/26, compared with
18/26 for GPT-4.1 mini alone. The policy accepted 14 decisions directly from
Jev, and all 14 were correct on this set.

## Results

| Classifier | Original 14 | Additional 12 | All 26 |
| --- | ---: | ---: | ---: |
| Jev 1.14 via OpenRouter | 13/14 | 12/12 | 25/26 |
| Clef Flash via OpenRouter | 13/14 | 8/12 | 21/26 |
| Clef via OpenRouter | 13/14 | Untested | Untested |
| Clairic via OpenRouter Extractions | 12/14 | Untested | Untested |
| GPT-4.1 mini via OpenAI | 10/14 | 8/12 | 18/26 |
| Jev first with GPT-4.1 mini fallback | 12/14 | 10/12 | 22/26 |

GPT's fallback errors were the mandatory initial contract, internship,
optional freelance engagement during probation, and description of the
existing team's working arrangements. Jev's earlier standalone error was
the listing that offered a contractor start alongside full-time employment.
Clef Flash tended to infer full-time employment from salary or hours alone.

The hybrid used GPT for 12 of 26 cases, a 54% reduction in chat-model requests.
GPT's measured median request latency was 1.68 seconds. The estimated hybrid
median was 0.49 seconds, calculated from the Jev request time plus the GPT
request time for cases routed to chat. These timings describe one run from
one host using two providers. The expanded set was hand-labeled for edge cases,
including cases that informed the routing rule. Treat these results as a
small regression benchmark. Production accuracy, throughput, and fallback
rates require measurement on real listing traffic.

## Model requests and routing

- Jev used `typesafe/jev-1.14-20261014`, Clef used `cloudflare/clef`, and Clef
  Flash used `cloudflare/clef-flash`. They received one four-option employment
  question through OpenRouter Decisions. The question asks which arrangements
  are available when joining, excluding client contracts and possible future
  conversions.
- Clairic used `cloudflare/clairic` through OpenRouter Extractions. It extracted
  separate verbatim spans for regular employment and flexible engagement.
  Each span had to occur in the input. The presence of both spans mapped to
  both options, the presence of one mapped to that option, and neither
  mapped to unknown.
- GPT used the observed snapshot `gpt-4.1-mini-2025-04-14` through OpenAI Chat
  Completions at temperature 0, with the scraper's prompt and structured
  response schema. The OpenRouter test credential returned HTTP 403 for GPT,
  so the successful GPT run used the direct OpenAI endpoint.
- The hybrid replay combined the worker's actual Jev acceptance logic with
  the GPT outputs for deferred cases. It accepts a single full-time or
  contract/part-time result at probability 0.90 or above when the keywords
  do not give a conflicting type. Mixed, unknown, lower-confidence, malformed,
  and failed responses go to chat. Listings over 2,000 UTF-8 bytes bypass
  Jev. Published board employment data takes precedence over either model.

The scraper code defines the exact prompt, schema, and routing checks.
[Classifier implementation](../packages/shared/src/five08/job_lead_sources.py)
[Worker configuration](configuration.md)
[Integration PR](https://github.com/508-dev/508-workflows/pull/442)

## Evaluation cases

**Full-time** means regular full-time employment from the start. **Contract**
includes freelance, fixed-term, mandatory initial contract-to-hire, and
part-time arrangements. **Both** means both options are available at the
start. **Unknown** includes internship-only listings and snippets without
clear employment terms. These map to `full_time`, `part_time`,
`part_time_or_full_time`, and `unknown` in the scraper.

### Original 14

| Case | Exact input | Expected | GPT | Hybrid route and output |
| --- | --- | --- | --- | --- |
| Permanent | Backend Engineer. Employment type: permanent, full-time. Hybrid in Tokyo. | Full-time | Full-time | Jev · Full-time |
| Freelance | Platform Engineer. Freelance engagement, invoices monthly, 32–40 hours per week. | Contract | Both | Jev · Contract |
| Optional contractor start | Full-time employee role. If preferred, you can start with reduced hours as a contractor before transitioning to full-time. | Both | Both | GPT · Both |
| Missing type | Data Engineer. Remote within Japan. ¥8M–¥12M. Experience with Python and Kafka. | Unknown | Unknown | GPT · Unknown |
| Negated contract | Full-time staff software engineer. We do not consider freelance or contract applicants. | Full-time | Full-time | Jev · Full-time |
| Client contracts | Permanent employee. You will lead our B2B platform and negotiate customer contracts. | Full-time | Full-time | Jev · Full-time |
| Part-time | Support Engineer. Part-time staff, three days per week. | Contract | Contract | Jev · Contract |
| Fixed-term | Design Engineer. 12-month fixed-term employment contract, renewable, 40 hours per week. | Contract | Both | Jev · Contract |
| Contract to hire | Community Manager. Starts with a mandatory six-month contract period, with possible conversion afterward. | Contract | Unknown | GPT · Unknown |
| Probation | Regular full-time employment from day one with a six-month probationary period. | Full-time | Full-time | Jev · Full-time |
| Japanese employee | 雇用形態：正社員。試用期間3ヶ月。東京都内のハイブリッド勤務。 | Full-time | Full-time | Jev · Full-time |
| Japanese contract | 雇用形態：業務委託。週4日、月額報酬。 | Contract | Contract | Jev · Contract |
| Japanese mixed | 雇用形態：正社員または業務委託。勤務形態は相談可能。 | Both | Both | GPT · Both |
| Internship | Summer engineering internship. Paid, 40 hours per week for 10 weeks. | Unknown | Contract | GPT · Contract |

### Additional 12

| Case | Exact input | Expected | GPT | Hybrid route and output |
| --- | --- | --- | --- | --- |
| Salary only | Acme \| Site Reliability Engineer \| ¥10M–¥14M annually \| Remote. Maintain distributed systems and incident response. | Unknown | Unknown | GPT · Unknown |
| Subcontracting | Join our consultancy to build APIs and support our client delivery contracts. You will review statements of work. | Unknown | Unknown | GPT · Unknown |
| Hours only | Core collaboration hours are 10:00–15:00 JST, with flexible start and end times. Total 40 hours/week. | Unknown | Unknown | GPT · Unknown |
| Probation and optional freelance | The permanent role starts with a probation period. We can also offer a 6-month freelance engagement from day one if you prefer. | Both | Unknown | GPT · Unknown |
| W-2 only | Acme \| Software Engineer \| W-2 employee, permanent. Third-party recruiters and 1099 submissions will be ignored. | Full-time | Full-time | GPT · Full-time |
| No promise of conversion | We currently have approval for a contractor only. We may consider a permanent offer next year if budget permits. | Contract | Unknown | Jev · Contract |
| Agency support | Full-time Product Designer. We frequently hire contractors to support this person. No agency candidates for this opening. | Full-time | Full-time | GPT · Full-time |
| Japanese negation | 正社員のみ募集。業務委託・副業での応募は受け付けません。 | Full-time | Full-time | Jev · Full-time |
| Japanese fixed-term | 有期契約社員（1年更新）、週40時間。正社員登用制度あり。 | Contract | Both | Jev · Contract |
| Permanent part-time | Permanent part-time employee, 20 hours per week. This role has no full-time option. | Contract | Contract | Jev · Contract |
| Existing team arrangements | We were founded by freelance engineers, and half the team works part-time. Opening: senior backend engineer. Apply with a portfolio. | Unknown | Both | GPT · Both |
| Temporary replacement | Cover parental leave on a nine-month fixed-term employment contract. The incumbent is expected to return. | Contract | Contract | Jev · Contract |
