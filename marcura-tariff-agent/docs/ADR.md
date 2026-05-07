# Architecture Decision Record — HarbourMind

**Status:** Active
**Last reviewed:** 2026-05-07
**Format:** Lightweight RFC. Each entry: *Context · Options considered · Decision · Consequences*.

This document captures the major design decisions taken during the build of
HarbourMind. It is the canonical reference for "why is the system shaped this
way?" — superseding any inline code comments or commit messages.

---

## ADR-001 · Free-form formula extraction over fixed calculators

### Context

The first iteration of HarbourMind used five fixed Python calculator
functions (`calculate_flat_fee`, `calculate_base_plus_incremental`,
`calculate_bracket_based`, `calculate_per_unit_per_period`,
`calculate_percentage_surcharge`) and asked Gemini to extract every
discovered tariff rule into the parameter shape of one of those functions.

End-to-end measurement: Gemini extracted 97 rules from the Durban tariff;
only 10 produced a charge. The other 87 raised silent `TypeError` in
`calc_func(**params)` because Gemini's parameter names did not match the
calculator's signatures. Final bill: **R 18,097 vs reference R ~580,000** —
under-billing by 97 % with no errors surfaced.

The root issue: *no rigid contract between a single extraction prompt and a
fixed calculator can survive ~8,000 distinct port tariffs each phrased in
their own way.*

### Options considered

| # | Option | Pro | Con |
|---|---|---|---|
| A | LLM produces `{formula: "<arithmetic string>", values: {...}}` and we evaluate the string with a sandboxed math engine | Gemini does interpretation (its strength); code does math (its strength); no fixed parameter schema | Requires sandboxed evaluator (third-party dependency) |
| B | LLM generates a Python function body, exec'd in a restricted namespace | Maximum expressiveness | Heavy sandboxing infra; audit/trace is harder; security risk if sandbox leaks |
| C | LLM does the whole calculation, returns a number | Simplest prompt | LLMs are unreliable at multi-step arithmetic; verifiability lost |

### Decision

**Adopt Option A.** Use `simpleeval` (single-purpose sandboxed expression
evaluator, ~500 LOC, no `eval`, no Python builtins) to evaluate the formula
string with the LLM-supplied values bound as names.

### Consequences

* Deleted ~400 LOC of rigid calculator code (`src/engine/calculators.py`,
  `CalculationAgent`).
* Added `src/engine/per_rule_calculator.py` (~700 LOC including prompts).
* Bottom line jumped from R 18,097 to R ~1.7M on the first run with the new
  approach (revealed the *next* problem — over-stacking, see ADR-003).
* Every charge now ships with a human-readable formula string in the trace
  (`(gt/100)*basic + (gt/100)*incremental*days_alongside`), enabling full
  audit at the line-item level.

---

## ADR-002 · Three-state output (`computed` / `not_applicable` / `needs_clarification`)

### Context

A binary "compute or skip" output was structurally wrong for tariff
calculations. Some rules clearly apply (compute). Some clearly don't apply
(tanker-only rule for a bulker — skip). But many rules **might** apply yet
the vessel certificate doesn't carry the data needed to decide:

* Cargo dues split by route (coastwise / foreign-going / transhipment) —
  certificate doesn't declare route.
* Light dues split by registration class (SA-registered / coaster /
  foreign-going) — certificate doesn't declare registration.
* OWH / late-arrival / cancellation surcharges — certificate has dates, not
  trigger flags.

Forcing every rule into compute-or-skip caused either over-billing
(computing them all) or under-billing (skipping them all).

### Options considered

| # | Option | Pro | Con |
|---|---|---|---|
| A | Two-state, with conservative bias: skip if uncertain | Simple | Under-bills systematically; user can't recover the missing charges without re-running with assumed inputs |
| B | Two-state, with aggressive bias: compute if plausible | Captures all charges | Over-bills systematically; stacks mutually-exclusive variants |
| C | Three-state: `computed`, `not_applicable`, `needs_clarification` (returned alongside computed charges) | Surfaces ambiguity to the integrating UI / human; no silent dropping | Requires the consumer to handle a third bucket in the response |

### Decision

**Adopt Option C.** The per-rule LLM is instructed to return a `status`
field of `"computed" | "not_applicable" | "needs_clarification"` along with
its formula and values. The `PerRuleCalculator` sorts results into three
output buckets surfaced separately in the API response.

### Consequences

* The response shape grows three new fields: `skipped_rules`,
  `skipped_count`, `needs_clarification`, `clarification_count`.
* Real port-cost workflows match this pattern (humans review ambiguous
  charges before invoicing).
* Integration burden: the UI has to surface clarifications. Acceptable —
  the alternative (silent over- or under-billing) is worse.

---

## ADR-003 · Sibling-context grouping to prevent variant stacking

### Context

After ADR-001 + ADR-002 landed, the system was correctly *finding* charges
but **stacking** mutually-exclusive variants. Concretely: the Durban tariff's
cargo-dues section contains 11 sibling rules (`cargo_dues_dry_bulk`,
`cargo_dues_breakbulk_iron_ore`, `cargo_dues_coastwise_breakbulk_bulk`,
`cargo_dues_transhipped_other`, etc.). Each, viewed in isolation, looked
plausible to the per-rule LLM — so all 11 returned `status: computed`.
Result: cargo dues alone exceeded R 4M.

The per-rule LLM was being called once per rule with no awareness of the
other 10 sibling rules in the same call.

### Options considered

| # | Option | Pro | Con |
|---|---|---|---|
| A | Pass *all* extracted rules into every per-rule prompt as context | LLM has full picture | Token-expensive; context bloats; LLM still doesn't know which rules are siblings |
| B | Pre-compute groups in Python (by charge-type prefix), pass each rule its sibling list | Cheap, deterministic, explicit | Requires a heuristic for grouping |
| C | Single batched call covering the whole rule set | Maximal coherence | Loses parallelism; one bad rule poisons the batch |

### Decision

**Adopt Option B.** Before dispatching the per-rule batches, build a
sibling index keyed on the first two underscore-separated tokens of the
charge type: `cargo_dues_*` → group `cargo_dues`, `port_dues_*` → group
`port_dues`. Each per-rule prompt receives `sibling_count` and
`sibling_charge_types`. The prompt instructs: *if `sibling_count >= 1` and
the vessel data does not uniquely select one variant, mark all of them
`needs_clarification`.*

### Consequences

* Stacking dropped to zero on the validation set (Durban + SUDESTADA).
* The grouping heuristic (first two tokens) works for the document we have;
  tested informally to be robust to common naming patterns. Future risk: a
  tariff that uses different prefixing (e.g. `dues_cargo_*` instead of
  `cargo_dues_*`) would not group correctly. Mitigation: switch to LLM-driven
  group inference if observed.

---

## ADR-004 · Parallel batched per-rule dispatch

### Context

After ADR-001/002/003, accuracy was within 3.1 % of the reference. But the
single-rule sequential dispatch took **~9 minutes (547 s)** for 112 rules
because every rule was a separate LLM round-trip.

### Options considered

| # | Option | Pro | Con |
|---|---|---|---|
| A | Sequential, single rule per call | Simplest; clean failure isolation per rule | 9-minute wall time |
| B | Sequential, all rules in one giant call | One round-trip | Poor reliability; one malformed JSON kills the whole bill; context bloat |
| C | Parallel, one rule per call (asyncio.gather) | ~10× speedup | Per-LLM-call cost stays the same |
| D | Parallel + batched (5 rules per call, 10 batches in flight) | ~30× speedup, ~5× fewer API calls | Slight reliability cost: a batch failure loses 5 outcomes (mitigated by per-batch fallback that converts to "skipped" with reason) |

### Decision

**Adopt Option D.** Build batches of 5 rules each in
`PerRuleCalculator.execute()`, dispatch via `asyncio.gather` capped by
`asyncio.Semaphore(10)`. Each batch sends one prompt that returns a JSON
**array** of 5 outcomes; if the array length doesn't match the request,
fall back to per-rule "skipped" entries (never silently drop).

### Consequences

* Wall time: 547 s → 17 s for 112 rules (32× speedup).
* API call count: 112 → ~22 (5× reduction).
* Per-batch error handling adds ~30 lines; no single rule failure cascades.

---

## ADR-005 · `thinking_budget = 0` for per-rule calls

### Context

Initial cost telemetry showed billing far above the visible token count.
Investigation: Gemini 2.5 Flash bills internal "thinking" tokens
(`thoughtsTokenCount` in the API response) on top of the visible
`candidatesTokenCount`. For our complex per-rule prompt, thinking tokens
were 60–80 % of total output cost. Across 112 rules × 18 test runs, this
hit the project's monthly spend cap twice.

### Options considered

| # | Option | Pro | Con |
|---|---|---|---|
| A | Keep thinking on (default) | Best LLM reasoning quality | 5–10× more expensive |
| B | Switch to a cheaper model (`gemini-2.5-flash-lite`) | 8× cheaper | Accuracy regressed materially on the nuanced anti-stacking logic; subtotal swung to R 1.5M |
| C | Keep `gemini-2.5-flash` but disable thinking via `thinking_budget=0` for the per-rule task only | Same accuracy; ~10× cheaper | Slight risk on novel rule shapes (mitigated by the structured nature of the per-rule task — there is no exploratory reasoning needed; the LLM is filling in a fixed template) |

### Decision

**Adopt Option C.** Per-rule LLM is constructed with `thinking_budget=0`.
Rule extraction (a less structured task) keeps thinking on (default).

### Consequences

* Per-bill cost dropped from ~R 1 to under R 0.10 (>10×).
* Reproducibility *improved* slightly because there is no internal
  reasoning to vary; the model behaves more deterministically on
  structured fill-in tasks.

---

## ADR-006 · Cargo dues default to `needs_clarification`

### Context

Durban (and most major port) tariffs include cargo dues — fees levied on
the cargo, not on the vessel. By convention these are billed on a
**separate invoice** issued to the cargo agent (consignee/shipper), not on
the vessel's port-call invoice. Marcura's reference total for SUDESTADA at
Durban (R 582,855) excludes cargo dues; our raw computation (which
extracts and applies the iron-ore-export rate of R 10.65/MT × 40,000 MT =
R 426,000) included them, blowing the bill 70 % above reference.

### Options considered

| # | Option | Pro | Con |
|---|---|---|---|
| A | Compute cargo dues normally; let the integrating system filter them out | Simplest | Default behaviour is misleading vs industry convention |
| B | Hardcode "exclude cargo_dues_*" in code | Matches Marcura's convention | Hardcoding port-domain knowledge in code; fights the "any port" claim |
| C | Encode the convention in the per-rule prompt: cargo dues default to `needs_clarification` regardless of available vessel data, surfacing them as user-resolvable | Aligns with universal billing convention; user explicitly opts in to include cargo dues; works for any port without code change | Cargo-dues-bearing port calls require a UI step to resolve |

### Decision

**Adopt Option C.** The per-rule prompt's anti-stacking section instructs:
*"CARGO DUES (cargo_dues_*) → ALWAYS `needs_clarification`. Cargo dues are
conventionally billed by the cargo agent on a separate invoice…"* The user
sees cargo dues in the clarifications bucket with the candidate rules and
can decide to compute or exclude.

### Consequences

* Default subtotal lands within 3.1 % of Marcura's vessel-side reference.
* Cargo dues are not silently dropped — they're explicitly surfaced for
  decision.
* Convention works port-agnostically (Singapore, Rotterdam, Mumbai etc all
  follow this split-billing pattern).

---

## ADR-007 · Content-hash disk caching

### Context

LlamaParse takes ~90 s per PDF. Rule extraction takes ~30 s. On a fresh
run, 60–70 % of the wall-clock time is spent on these two steps. During
testing the same Durban tariff PDF was parsed dozens of times.

### Options considered

| # | Option | Pro | Con |
|---|---|---|---|
| A | No cache | Simple | 547 s baseline; LlamaParse cost recurring |
| B | Redis / external cache | Centralised; survives container restarts | Operational dependency; overkill for single-instance deploys |
| C | On-disk cache keyed by SHA-256 of the input bytes (PDF) and parsed text + port name (rules) | Zero infra; survives restarts; deterministic key; collision-free in practice | Per-instance only (acceptable for single-instance demo deploys) |

### Decision

**Adopt Option C.** Two caches:

* `data/.parse_cache/<sha256(pdf_bytes)>.md` — LlamaParse markdown output.
* `data/.rule_cache/<sha256(parsed_text + port_name)>.json` — RuleStore
  Pydantic JSON.

Both writes are best-effort (a cache write failure does not break the
request).

### Consequences

* Cold first run: ~3 min. Hot re-run with both caches warm: ~17 s.
* Cache directory is gitignored. In Docker, the cache lives in the
  container — a fresh container is cold. For multi-instance deployments,
  Option B (Redis) becomes the correct choice.

---

## ADR-008 · Per-period field disambiguation (`days_alongside` vs `days_in_port`)

### Context

The vessel certificate carries both `days_alongside` (3.39) and computed
`days_in_port` (7.0 from arrival/departure timestamps). Different per-period
charges in the same tariff use different conventions:

* "for vessels at berth … per 24-hour period" → `days_alongside`
* "from passing entrance inwards until passing entrance outwards" →
  `days_in_port`

The per-rule LLM was inconsistent in choosing the right field, causing
~17 % subtotal variance between consecutive runs of the same calculation.

### Options considered

| # | Option | Pro | Con |
|---|---|---|---|
| A | Accept variance | None | Unacceptable for a billing system |
| B | Force `days_alongside` always | Deterministic | Wrong against documents that explicitly say "in port" |
| C | Encode an explicit decision rule in the per-rule prompt: keyword match on the rule's `calculation_logic` text | Deterministic AND respects document intent | Requires defining the keyword mapping |

### Decision

**Adopt Option C.** The per-rule prompt has an explicit table:

| Rule text contains | Use field |
|---|---|
| "at berth" / "alongside" / "while moored" / "berth hire" | `days_alongside` |
| "in port" / "while in port" / "at anchor" / "port stay" | `days_in_port` |
| (no qualifier) | `days_alongside` (default) |

Plus a rounding rule: **never** apply `ceil()` to fractional days, even
when the rule says "or part thereof" — pass the exact value (e.g. 3.39).

### Consequences

* Three back-to-back runs now return the **same subtotal to the cent**
  (R 564,840.07).
* Variance from intra-call LLM non-determinism reduced to ≤ R 1 (floating
  point on the tug fee in some runs).
* Documented convention; portable to other tariffs.

---

## Index of decisions

| ID | Title | Component touched |
|---|---|---|
| ADR-001 | Free-form formula extraction over fixed calculators | `per_rule_calculator.py` |
| ADR-002 | Three-state output | `per_rule_calculator.py`, response shape |
| ADR-003 | Sibling-context grouping | `per_rule_calculator.py` |
| ADR-004 | Parallel batched per-rule dispatch | `per_rule_calculator.py` |
| ADR-005 | `thinking_budget = 0` for per-rule calls | LLM construction |
| ADR-006 | Cargo dues default to `needs_clarification` | per-rule prompt |
| ADR-007 | Content-hash disk caching | `pdf_parser.py`, `main.py` |
| ADR-008 | Per-period field disambiguation | per-rule prompt |
