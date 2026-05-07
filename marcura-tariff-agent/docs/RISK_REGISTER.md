# Risk Register — HarbourMind

**Status:** Active
**Last reviewed:** 2026-05-07
**Review cadence:** At each material architecture change. Minimum quarterly.

This register catalogues the known risks to HarbourMind's correctness,
availability, cost profile, and security posture, and the mitigations in
place. Each risk has an ID for cross-referencing from the API reference
and ADR.

**Severity scale:** *Low* (operational nuisance) · *Medium* (correctness or
cost impact, contained) · *High* (could produce a wrong bill or block the
service) · *Critical* (security or legal exposure).

**Status legend:** *Open* (no mitigation yet) · *Mitigated* (mitigation in
place but residual risk remains) · *Accepted* (residual risk acknowledged,
no further action planned).

---

## R-001 · LLM non-determinism in rule interpretation

| Attribute | Value |
|---|---|
| Severity | High |
| Status | Mitigated |
| Last incident | 2026-05-06 (resolved) |

### Description

Even at `temperature=0`, Gemini 2.5 Flash exhibits intrinsic
non-determinism. During development, three back-to-back runs of the same
calculation produced subtotals of R 481K, R 509K, and R 565K — a 17 %
swing.

Root causes identified:

* Inconsistent choice between `days_alongside` and `days_in_port` for
  per-period charges.
* Inconsistent application of the per-service multiplier
  (`number_of_operations`).
* Inconsistent rounding (`ceil(days)` vs raw fractional days) for "or
  part thereof" clauses.

### Mitigation

* Per-rule prompt now contains explicit deterministic decision tables
  (see ADR-008) for period-field selection and rounding policy.
* `thinking_budget=0` removes the variance source of internal LLM
  reasoning paths (see ADR-005).
* Three back-to-back runs now return the same subtotal to the cent.

### Residual risk

Edge cases not covered by the explicit decision tables (e.g. a tariff
that uses unfamiliar period vocabulary like "per tide" or "per turnaround")
could re-introduce non-determinism. **Detection:** the integrating system
should flag any change in subtotal across re-runs of the same inputs as a
data-quality anomaly worth investigating.

---

## R-002 · LlamaParse availability and latency

| Attribute | Value |
|---|---|
| Severity | Medium |
| Status | Mitigated |

### Description

LlamaParse is an external SaaS dependency. Outages or rate-limiting on
their end produce 5xx responses; sustained latency on their end produces
job timeouts after 5 minutes (the polling cap in `pdf_parser.py`).

### Mitigation

* **Disk cache** keyed by `SHA-256(pdf_bytes)`: a previously-parsed PDF is
  served from local disk indefinitely, fully bypassing LlamaParse on
  cache hits (ADR-007).
* **Surfaced errors:** failures return HTTP 400 with `code:
  PDF_PROCESSING_ERROR` and a clear `message` rather than masking as a
  generic 500.
* **Polling timeout** capped at 5 minutes prevents indefinite hangs.

### Residual risk

A first-time parse of a brand-new tariff PDF still requires LlamaParse
availability. **Plan:** evaluate fallback parsers (PyMuPDF + table
extraction heuristics, or AWS Textract) as a hot-standby. Open as
*future work*.

---

## R-003 · Gemini quota / spend cap

| Attribute | Value |
|---|---|
| Severity | Medium |
| Status | Mitigated |

### Description

Gemini 2.5 Flash bills "thinking" tokens internally (not visible in the
response). Per-request token spend is therefore higher than the visible
output suggests. Hitting Google's project-level monthly spend cap returns
HTTP 429 and blocks all subsequent requests until the cap is raised or
the calendar month resets.

During development this cap was hit twice on a $5 monthly limit because
of the cumulative cost of repeated test runs.

### Mitigation

* **`thinking_budget=0`** for per-rule calls (the dominant LLM cost path)
  cuts internal reasoning tokens to zero (ADR-005).
* **5-rule batching** reduces total LLM calls per bill from ~110 to ~22
  (ADR-004).
* **Caches** make repeated runs of the same input free.

### Residual risk

* High-volume production usage could still hit project-level spend
  caps. **Plan:** monitor the cumulative monthly spend via Google Cloud
  Billing alerts; alarm at 60 % of the cap.
* Quota policy on the Gemini side could change without notice.
  **Plan:** abstract the LLM constructor (already a single function in
  `_build_llm`) so a swap to OpenAI / Anthropic / Vertex AI is a
  one-line change.

---

## R-004 · Tariff format drift

| Attribute | Value |
|---|---|
| Severity | Medium |
| Status | Mitigated |

### Description

A new tariff document might use unfamiliar phrasing, table structures, or
charge categorisation that the rule-extraction prompt and sibling-grouping
heuristic don't handle correctly. Two specific concerns:

* **Sibling grouping** uses the first two underscore-separated tokens of
  the LLM-generated `charge_type` as the group key (ADR-003). A tariff
  whose charge labels don't follow common naming patterns (e.g.
  `dues_cargo_*` instead of `cargo_dues_*`) would group incorrectly.
* **Anti-stacking conventions** in the per-rule prompt (cargo dues default
  to `needs_clarification`, surcharges default to `not_applicable`)
  assume universal maritime billing patterns. A tariff that violates
  those conventions would mis-classify.

### Mitigation

* Rule extraction prompt is explicit about preserving document-source
  charge_type names so labels remain stable.
* Sibling grouping is heuristic-based but observable: anomalies surface
  as either over-billing (no clarifications) or under-billing (excessive
  clarifications), both of which a reviewing human will catch.
* All extraction is read-only against the input — no
  hardcoded port-specific logic.

### Residual risk

* Multi-port validation has not yet been performed. **Plan:** validate
  against tariffs from at least 2 additional major ports (Singapore,
  Rotterdam suggested) before claiming general "any port" coverage.
* If a tariff is in a non-English language, LlamaParse and Gemini both
  support multilingual reading, but the prompt's English-keyword
  heuristics ("at berth", "in port", "or part thereof") would not match.
  **Plan:** translate keyword tables to a localisation layer if a
  non-English tariff comes into scope.

---

## R-006 · Vessel certificate sparsity

| Attribute | Value |
|---|---|
| Severity | Medium |
| Status | Mitigated |

### Description

A standard shipping certificate is intentionally thin — it identifies the
vessel and the call, but does not carry the full operational context
needed to apply every tariff rule deterministically. Concrete gaps:

* No cargo direction (`import` / `export` / `coastwise` / `transhipment`)
  declared explicitly.
* No service-time-of-day (needed for OWH / weekend surcharges).
* No drydock booking flag.
* No service-duration data for hourly equipment hire.
* No party-count for split-account fees.

Computing these charges blindly would over-bill. Skipping them would
under-bill.

### Mitigation

The three-state output (ADR-002) surfaces these as
`needs_clarification` entries with explicit `missing_inputs[]` arrays.
The integrating UI is responsible for resolving them with operator input
or external data (manifest, port operations log, B/L) before final
invoicing.

### Residual risk

Operators who skip the clarification step and post the bill as-is will
under-bill on calls with cargo dues or other clarification-bound charges.
**Detection:** the `clarification_count` field in the response is
non-zero whenever there are unresolved items — integrators should treat
non-zero as a "needs review before billing" signal.

---

## R-007 · Cache poisoning / stale cache

| Attribute | Value |
|---|---|
| Severity | Low |
| Status | Mitigated |

### Description

The on-disk cache is keyed by `SHA-256(input_bytes)`. If a tariff PDF is
updated in place (same filename) but actually has different content,
the SHA-256 will differ and the cache won't be hit — correct behaviour.
If somehow two different PDFs produce the same SHA-256, the cache would
return the wrong content (cryptographic collision).

### Mitigation

SHA-256 collisions are not practical to engineer at present. The cache
key is also tied to *byte-for-byte* content, so even minor edits to a
PDF (e.g. re-saved with a different writer) produce a fresh cache entry.

### Residual risk

If the LlamaParse output format itself changes (e.g. a server-side
upgrade produces different markdown for the same input), the cached
markdown becomes stale. **Plan:** include a `LLAMAPARSE_VERSION`
component in the cache key when LlamaParse exposes one. Currently
`Open / minor`.

---

## R-008 · Concurrent request safety

| Attribute | Value |
|---|---|
| Severity | Low |
| Status | Mitigated |

### Description

The application is async-first (FastAPI + asyncio). Concurrent requests
can interleave. Two specific concerns:

* **Cache writes:** two concurrent first-time calls for the same PDF
  could both invoke LlamaParse and both write to the cache. Wasted work
  but no correctness issue (writes are idempotent — same key, same
  content).
* **In-memory log store** (`_calculation_logs` dict in `main.py`) is a
  process-local dict. Multiple workers don't share state. The logs are
  diagnostic, not a system of record — accepting this limitation.

### Mitigation

* Cache writes are atomic per file (single `write_text` call); concurrent
  writes don't corrupt the file.
* No shared mutable state beyond the diagnostic logs dict.

### Residual risk

Multi-worker production deployment would lose log visibility across
workers. **Plan:** if the diagnostic logs become operationally
important, move them to a shared store (sqlite, Redis, or a logging
backend).

---

## R-010 · Lack of authentication on public endpoints

| Attribute | Value |
|---|---|
| Severity | High |
| Status | **Open** |

### Description

The `/api/v1/calculate-from-pdfs` endpoint accepts PDF uploads from any
caller. In a public deployment this would allow unbounded LlamaParse and
Gemini cost (hundreds of dollars per hour at sustained abuse rates).

The application is shipped without authentication on the assumption that
it sits behind a reverse proxy / API gateway responsible for auth.

### Mitigation

None in the application itself. The README states this explicitly.

### Plan

* Document the deployment expectation in the README and DEPLOYMENT.md.
* Provide an example deployment with Cloud Run + IAP / API Gateway +
  rate limiting as a reference.
* Optionally add a simple `Authorization: Bearer <token>` middleware
  with token-rotation support, controllable via `.hmenv.txt`.

---

## Risk index

| ID | Title | Severity | Status |
|---|---|---|---|
| R-001 | LLM non-determinism in rule interpretation | High | Mitigated |
| R-002 | LlamaParse availability and latency | Medium | Mitigated |
| R-003 | Gemini quota / spend cap | Medium | Mitigated |
| R-004 | Tariff format drift | Medium | Mitigated |
| R-006 | Vessel certificate sparsity | Medium | Mitigated |
| R-007 | Cache poisoning / stale cache | Low | Mitigated |
| R-008 | Concurrent request safety | Low | Mitigated |
| R-010 | Lack of authentication on public endpoints | High | **Open** |
