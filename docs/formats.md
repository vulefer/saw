# Saw data formats

This document defines Saw's public data-format contracts. The JSON problem
format is versioned independently from the Saw package and from the Python
problem model. In this document, **must**, **must not**, and **may** are
normative requirements.

## JSON problem format version 1

Version 1 is the canonical JSON representation of a Saw problem. It serializes
only problem input. It does not define solution JSON, a command-line interface,
or parser behavior beyond the validation and compatibility rules below.

The companion structural schema is
[`schemas/problem-v1.schema.json`](../schemas/problem-v1.schema.json). This
document remains authoritative for rules that JSON Schema cannot express,
including duplicate object member names, integer spelling, and ID uniqueness.

### Top-level object

A version 1 problem is one JSON object with exactly these members. All lengths
in every version 1 payload are positive integer millimetres (`mm`); the unit is
fixed by the contract and is therefore not repeated in the payload.

| Member | Required value |
| --- | --- |
| `format_version` | The JSON integer `1`. |
| `stocks` | An array of zero or more stock objects. |
| `demands` | An array of zero or more demand objects. |

All three members are required. Unknown members are invalid. A reader must
reject an unsupported `format_version`; it must not guess a version or silently
interpret another version as version 1. Empty `stocks` and `demands` arrays are
valid and retain the meanings defined in the problem specification.

`format_version` identifies the contract used to interpret the payload. It is
independent of the installed Saw package version: multiple package releases may
read or write format version 1. If a future format makes an incompatible change,
it will use a different value so readers can select the correct rules or reject
the payload safely instead of silently misinterpreting it.

### Stock objects

Every member of `stocks` is an object with exactly these required members:

| Member | Type and meaning |
| --- | --- |
| `stock_id` | A non-empty JSON string, unique among stock objects. |
| `material_id` | A non-empty JSON string. |
| `length` | A positive JSON integer measured in millimetres (`mm`). |
| `quantity` | A positive JSON integer giving the available stock count. |

Unknown or missing members are invalid.

### Demand objects

Every member of `demands` is an object with exactly these required members:

| Member | Type and meaning |
| --- | --- |
| `demand_id` | A non-empty JSON string, unique among demand objects. |
| `material_id` | A non-empty JSON string. |
| `length` | A positive JSON integer measured in millimetres (`mm`). |
| `quantity` | A positive JSON integer giving the exact required piece count. |

Unknown or missing members are invalid.

### JSON and primitive-value rules

- The input must be a JSON text as defined by RFC 8259. Duplicate member names
  in any JSON object are invalid, even if the repeated values are equal.
- `length` and `quantity` use JSON integer spelling: an optional minus sign
  followed by base-10 digits, with no decimal point or exponent. The value must
  be greater than zero. Therefore `1.0`, `1e0`, strings such as `"1"`, and the
  Boolean `true` are invalid; readers must not coerce them to integers.
- `format_version` uses the same integer spelling and must equal `1`.
- Numeric values have no smaller format-level upper bound. Backend or resource
  limits are separate from format validity and must not be reported as malformed
  JSON input.
- Every ID is preserved as its decoded JSON string value. Identity is exact and
  case-sensitive; readers must not trim or perform Unicode normalization.
  Consistent with the Python problem contract, a whitespace-only ID is non-empty
  and therefore valid.
- Stock IDs and demand IDs occupy separate namespaces, so a stock and a demand
  may have the same ID. Material IDs are equality values rather than references
  to positional or separately declared material records.

### Identity and ordering

`stock_id`, `demand_id`, and `material_id` are carried explicitly in every
record. Array position and object-member order have no semantic meaning. A
reader may preserve input order for deterministic diagnostics, but it must not
use an array index as record identity or change an ID. Reordering `stocks`,
`demands`, or object members represents the same mathematical problem.

JSON whitespace and escape spelling likewise have no semantic meaning after
decoding. Version 1 defines a canonical data model, not a byte-level JSON
canonicalization scheme.

### Unknown fields and format evolution

Version 1 is a closed contract: unknown fields are invalid at the top level and
inside stock and demand objects. Producers must not add extension fields to a
version 1 payload. A change that adds, removes, renames, or changes the meaning
or type of a field requires a new `format_version`.

### Valid request example

The following request is valid. It is also available as
[`examples/json/problem-v1-valid.json`](../examples/json/problem-v1-valid.json).

```json
{
  "format_version": 1,
  "stocks": [
    {
      "stock_id": "stock-10",
      "material_id": "steel",
      "length": 10,
      "quantity": 2
    }
  ],
  "demands": [
    {
      "demand_id": "demand-6",
      "material_id": "steel",
      "length": 6,
      "quantity": 1
    },
    {
      "demand_id": "demand-4",
      "material_id": "steel",
      "length": 4,
      "quantity": 1
    }
  ]
}
```

### Invalid request examples

This request is invalid because `notes` is an unknown top-level field:

```json
{
  "format_version": 1,
  "stocks": [],
  "demands": [],
  "notes": "extension fields are not allowed"
}
```

This request is invalid because the two stock objects have the same
`stock_id`, even though their other values differ:

```json
{
  "format_version": 1,
  "stocks": [
    {"stock_id": "stock-a", "material_id": "steel", "length": 10, "quantity": 1},
    {"stock_id": "stock-a", "material_id": "steel", "length": 20, "quantity": 1}
  ],
  "demands": []
}
```

This request is invalid because `6.0` uses decimal rather than integer JSON
spelling; no numeric coercion is permitted:

```json
{
  "format_version": 1,
  "stocks": [],
  "demands": [
    {"demand_id": "demand-6", "material_id": "steel", "length": 6.0, "quantity": 1}
  ]
}
```

Potential infeasibility is not a format error. For example, a non-empty demand
array with an empty stock array is a valid version 1 request whose feasibility
must be decided by a solver.

## Python problem contract

The current Python `Problem`, `Stock`, and `Demand` contract and the
mathematical meaning shared by both representations are defined in
[`problem-spec.md`](problem-spec.md). Version 1 JSON field names map directly
to those public model fields; `format_version` is JSON envelope metadata rather
than a field on `Problem`.
