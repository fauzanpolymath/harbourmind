# API Reference — HarbourMind

**Version:** 1.0.0
**Base URL:** `<base-url>` (replace with your deployment host)
**Format:** All requests and responses use `application/json` unless noted.
**Authentication:** None at present. The deployment is expected to sit behind
a reverse proxy or API gateway responsible for auth.

---

## 1. Endpoint summary

| Method | Path | Purpose | Body | Auth |
|---|---|---|---|---|
| `GET` | `/` | Static web UI (HTML) | — | none |
| `GET` | `/health` | Liveness probe | — | none |
| `POST` | `/api/v1/calculate-from-pdfs` | Upload tariff and vessel PDFs, receive itemised bill | `multipart/form-data` | none |
| `GET` | `/api/v1/logs` | List recent calculations (summary) | — | none |
| `GET` | `/api/v1/logs/{calculation_id}` | Full detail of one calculation | — | none |

---

## 2. `GET /health`

Liveness probe. Returns 200 OK if the application has started.

### Response

```json
{
  "status": "healthy",
  "service": "HarbourMind Tariff Calculator",
  "timestamp": "2026-05-07T03:42:00.123Z"
}
```

### Status codes

| Code | Meaning |
|---|---|
| 200 | Application healthy |

---

## 3. `POST /api/v1/calculate-from-pdfs`

The primary endpoint. Accepts a port tariff PDF and a vessel certificate
PDF, returns an itemised disbursement bill with full formula traceability,
plus separate buckets for skipped rules and rules that need user
clarification.

### Request — `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `tariff_pdf` | file (PDF) | yes | The port's published tariff document. Any port. Any layout. |
| `vessel_pdf` | file (PDF) | yes | A shipping certificate identifying the vessel and the call. |

### Example (cURL)

```bash
curl -X POST <base-url>/api/v1/calculate-from-pdfs \
  -F "tariff_pdf=@<path-to-tariff>.pdf" \
  -F "vessel_pdf=@<path-to-vessel-certificate>.pdf"
```

### Response (200 OK)

```jsonc
{
  "calculation_id": "calc_a1b2c3d4e5f6",
  "vessel_name": "SUDESTADA",
  "vessel_details": {
    "name": "SUDESTADA",
    "type": "Bulk Carrier",
    "gross_tonnage": 51300,
    "net_tonnage": 31192,
    "deadweight_tonnage": 93274,
    "length_overall": 229.2,
    "beam": 38.0,
    "draft": 14.9,
    "port": "Durban",
    "cargo_type": "Iron Ore",
    "cargo_tonnage": 40000,
    "days_alongside": 3.39,
    "number_of_operations": 2
  },
  "port": "South African Ports (Transnet National Ports Authority)",
  "charges": [
    {
      "charge_type": "port_dues_basic",
      "description": "Basic fee per 100 tons or part thereof, plus per 100 tons or part thereof per 24 hour period…",
      "amount": 309853.11,
      "confidence": 0.98,
      "trace": {
        "formula": "(gt / 100) * basic_rate + (gt / 100) * incremental_rate * days_alongside",
        "values": {
          "gt": 51300,
          "basic_rate": 192.73,
          "incremental_rate": 57.79,
          "days_alongside": 3.39
        },
        "explanation": "Port dues for vessel at berth, fractional days pro-rated.",
        "category_group": "port_dues",
        "extraction_confidence": 0.98,
        "unit": "ZAR"
      }
    }
    // … more charges
  ],
  "extraction": {
    "rules_count": 112,
    "average_confidence": 0.97
  },
  "subtotal": 564840.07,
  "currency": "ZAR",
  "tax_rate": 0.15,
  "tax_label": "VAT",
  "tax_amount": 84726.01,
  "grand_total": 649566.08,
  "processing_time_ms": 17240,
  "status": "success",
  "skipped_rules": [
    {
      "charge_type": "drydock_dues_durban",
      "reason": "Vessel is not drydocking; rule does not apply.",
      "category_group": "drydock",
      "missing_inputs": ["drydocking"],
      "rule_index": 42
    }
    // … more
  ],
  "skipped_count": 99,
  "needs_clarification": [
    {
      "category_group": "cargo_dues",
      "candidates": [
        "cargo_dues_dry_bulk",
        "cargo_dues_breakbulk_iron_ore_exports",
        "cargo_dues_coastwise_breakbulk_bulk",
        "cargo_dues_transhipped_other_cargo"
      ],
      "missing_inputs": ["cargo_direction", "cargo_route"],
      "reason": "Multiple cargo-dues variants apply; vessel data does not select one.",
      "candidate_count": 4
    }
    // … more groups
  ],
  "clarification_count": 1,
  "note": "All values extracted from PDFs - zero hardcoding"
}
```

### Field reference

#### Top level

| Field | Type | Notes |
|---|---|---|
| `calculation_id` | string | Stable identifier of this calculation. Pass to `/logs/{id}` for re-fetch. |
| `vessel_name` | string \| null | Extracted from the vessel certificate. |
| `vessel_details` | object | Full extracted `VesselProfile`. See § 3.1. |
| `port` | string | Extracted port name. |
| `charges` | array<Charge\> | Successfully computed line items. See § 3.2. |
| `extraction.rules_count` | integer | Total rules pulled from the tariff. |
| `extraction.average_confidence` | float | Mean of `extraction_confidence` across rules. |
| `subtotal` | float | Sum of `charges[].amount`. |
| `currency` | string \| "" | ISO-4217 code extracted from the tariff document (e.g. `"ZAR"`, `"USD"`). Empty string if the tariff is silent on currency. |
| `tax_rate` | float | Tax rate extracted from the tariff (e.g. `0.15` for 15 %). `0.0` if the tariff does not declare an applicable local tax. |
| `tax_label` | string | Tax label as written in the tariff (e.g. `"VAT"`, `"GST"`). `"Tax"` if not specified. |
| `tax_amount` | float | `subtotal * tax_rate`. |
| `grand_total` | float | `subtotal + tax_amount`. |
| `processing_time_ms` | integer | Wall clock time for this request, including LlamaParse. |
| `status` | `"success"` \| `"error"` | — |
| `skipped_rules` | array<Skip\> | Rules deliberately not applied; each carries a reason. |
| `skipped_count` | integer | `len(skipped_rules)`. |
| `needs_clarification` | array<Clarification\> | Rule groups requiring user input to resolve. |
| `clarification_count` | integer | `len(needs_clarification)`. |
| `note` | string | Free-text annotation from the calculator. |

#### 3.1 VesselProfile (response.vessel_details)

The vessel profile is the parser's full output. Fields are present only
when the source certificate carries them (`null` values are dropped from
the response via `exclude_none`).

Fields are grouped by what role they play in the bill — only some drive
calculations; others are identity / audit, and a few are extracted in
case future port tariffs need them.

##### Calculation drivers — referenced in computed formulas

| Field | Type | Unit | Drives |
|---|---|---|---|
| `gross_tonnage` | float | metric tonnes (GT) | Port dues, light dues, VTS, pilotage, towage bracket lookup |
| `days_alongside` | float | days | Per-period component of port dues (e.g. 3.39) |
| `number_of_operations` | integer | — | Per-service multiplier for pilotage and tug assistance (typically 2: in + out) |

##### Situational — referenced by specific rule families when present

| Field | Type | Unit | Used by |
|---|---|---|---|
| `length_overall` | float | metres | Pilotage exemption brackets, small-vessel rules, hulks per-metre charges |
| `cargo_type` | string | — | Cargo-dues commodity rate lookup (currently surfaced as clarification — see ADR-006) |
| `cargo_tonnage` | float | metric tonnes | Cargo-dues amount calculation (clarification path) |

##### Identity / audit — not used in calculations

| Field | Type | Notes |
|---|---|---|
| `name` | string | Vessel name (response label, audit) |
| `imo_number` | string \| null | IMO identifier (audit) |
| `call_sign` | string \| null | Radio call sign (audit) |
| `type` | string \| null | e.g. "Bulk Carrier" — audit only; no current rule conditions on type |
| `port` | string \| null | Port of call (response label) |

##### Extracted but currently unreferenced

The parser extracts these because other ports' tariffs may use them; the
Durban tariff in the validation set does not. They appear in
`vessel_details` for transparency, but no current formula references them:

`net_tonnage`, `deadweight_tonnage`, `beam`, `draft`, `days_in_port`.

> If you write an integration today and rely solely on the *driver* fields
> above, you'll get the same result as if you used all sixteen.

#### 3.2 Charge (response.charges[])

Every charge ships with a complete formula trace so the line can be
audited end-to-end without re-running the calculation.

| Field | Type | Always present? | Notes |
|---|---|---|---|
| `charge_type` | string | yes | Lowercase snake_case label as found in the document |
| `description` | string | yes | The rule's `calculation_logic` text from the tariff |
| `amount` | float | yes | Computed amount, in the tariff's currency |
| `trace.formula` | string | yes | The arithmetic expression evaluated, e.g. `(gt/100) * rate * days_alongside` |
| `trace.values` | object<string, number\> | yes | Bound values used in the formula. Inspect this to see which `VesselProfile` drivers were used. |
| `trace.explanation` | string | yes | LLM-supplied 1-sentence reasoning. Documentation only — does not affect the calculation. |
| `trace.category_group` | string \| null | yes | Sibling-grouping key (e.g. `port_dues`). `null` if the rule stands alone. |

##### Fields kept for back-compat (will be removed in `v2`)

| Field | Type | Notes |
|---|---|---|
| `confidence` | float | The rule's `extraction_confidence`. Identical to `trace.extraction_confidence`. |
| `trace.extraction_confidence` | float | Same value as `confidence`. |
| `trace.unit` | string | Currency code per the trace (legacy field). Top-level `currency` is the canonical source. |

#### 3.3 Skip (response.skipped_rules[])

| Field | Type | Notes |
|---|---|---|
| `charge_type` | string | The skipped rule's identifier |
| `reason` | string | Human-readable explanation (e.g. "Vessel does not match LOA bracket 30–50 m") |
| `category_group` | string \| null | If present |
| `missing_inputs` | array<string\> | Fields the rule would need to apply (often empty for "definitely not applicable") |
| `rule_index` | integer | Position of the rule in the original RuleStore |

#### 3.4 Clarification (response.needs_clarification[])

Clarifications are **grouped** — multiple sibling rules from the same
category appear as a single clarification entry rather than N entries.

| Field | Type | Notes |
|---|---|---|
| `category_group` | string \| null | Group label, e.g. `cargo_dues`, `light_dues`. `null` for ungrouped |
| `candidates` | array<string\> | Charge types in this clarification group |
| `missing_inputs` | array<string\> | Vessel fields needed to disambiguate |
| `reason` | string | Why the rule(s) need clarification |
| `candidate_count` | integer | `len(candidates)` |

### Status codes

| Code | Meaning |
|---|---|
| 200 | Calculation completed (may include clarifications) |
| 400 | One of the PDFs could not be parsed or extracted; details in `detail.message` |
| 422 | Missing or malformed `multipart/form-data` field |
| 500 | Internal error (e.g. LLM provider unavailable). Retry. |

### Errors

```json
{
  "detail": {
    "message": "Failed to extract tariff from PDF: <reason>",
    "code": "PDF_PROCESSING_ERROR"
  }
}
```

| `code` | Trigger |
|---|---|
| `PDF_PROCESSING_ERROR` | LlamaParse failure, rule extraction failure, or vessel parser failure |
| `INVALID_INPUT` | Pydantic validation rejected the parsed JSON |
| `CALCULATION_ERROR` | Unexpected exception during the per-rule calculation pass |

---

## 4. `GET /api/v1/logs`

Returns a summary list of recent calculations, newest first.

### Query parameters

| Param | Type | Default | Notes |
|---|---|---|---|
| `limit` | integer | 100 | Maximum entries to return |

### Example

```bash
curl <base-url>/api/v1/logs?limit=10
```

### Response (200 OK)

```json
{
  "total": 12,
  "returned": 10,
  "calculations": [
    {
      "calculation_id": "calc_a1b2c3d4e5f6",
      "timestamp": "2026-05-07T03:42:01.000Z",
      "vessel_name": "SUDESTADA",
      "port": "South African Ports (Transnet National Ports Authority)",
      "grand_total": 649566.08,
      "status": "success",
      "processing_time_ms": 17240
    }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `total` | integer | All known calculations (not paginated) |
| `returned` | integer | Number actually included in this response |
| `calculations` | array | Summaries (one per calculation) |

---

## 5. `GET /api/v1/logs/{calculation_id}`

Full detail of one previously executed calculation. Returns the same shape
as `POST /calculate-from-pdfs` would have returned.

### Path parameters

| Param | Type | Notes |
|---|---|---|
| `calculation_id` | string | The id returned by a prior `POST /calculate-from-pdfs` |

### Example

```bash
curl <base-url>/api/v1/logs/calc_a1b2c3d4e5f6
```

### Status codes

| Code | Meaning |
|---|---|
| 200 | Found |
| 404 | No calculation with that id |

---

## 6. Performance & cost notes

| Scenario | Wall time | LLM calls | Approx cost |
|---|---:|---:|---:|
| Cold run (both caches empty) | ~3 min | ~28 (1 rule extraction + ~25 per-rule batches + 1 vessel + 1 vessel-text) | ~$0.01 |
| Hot run (both caches warm) | ~17 s | ~25 per-rule batches | ~$0.01 |
| Repeat with no rule changes | ~17 s | ~25 | ~$0.01 |

Caches are content-hashed on disk. Re-uploading the **same** tariff PDF
hits the parse cache; re-running with the **same** parsed text + port name
hits the rule cache.

---

## 7. Versioning

The endpoint is versioned at the URL level (`/api/v1/...`). Breaking
changes will be released under `/api/v2/...` while `v1` remains available
for at least one deprecation window.

---

## 8. Open issues

* `confidence` field is repeated at the top of `charges[]` for backward
  compatibility with an older response shape. Will be removed in v2.
* `trace.unit` (per-charge) duplicates top-level `currency`. Kept for
  back-compat; will be removed in v2.
