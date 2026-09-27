# Dynamic `llama-server` Argument UI Plan

**Status:** Proposed, separate from the main implementation plan  
**Plan date:** 2026-09-21  
**Reference:** [`docs/llama-server-v0.4.1-cli-reference.md`](../llama-server-v0.4.1-cli-reference.md)  
**Scope:** Dynamically describe, validate, render, explain, and persist the complete `llama-server` launch-argument surface.

---

## 1. Goals & Principles

Transition profile configuration from static, hand-coded fields to a data-driven catalog system while keeping the control plane strictly isolated from arbitrary command execution.

- **Dual-source truth:** A versioned JSON catalog provides metadata (descriptions, schemas, categories, effect tags), while the selected runtime's probed `--help` output governs actual compatibility.
- **Progressive disclosure:** Safe, high-frequency arguments appear in a default Simple mode; the full catalog, uncatalogued options, and custom overrides live in Advanced mode.
- **Preservation & safety:** Unsupported, deprecated, or catalog-mismatched flags are never discarded on runtime switches; they are preserved via a passthrough "Custom Parameters" container.
- **In-line context:** Every argument features an accessible `?` control opening responsive documentation with directional effect tags (`vram`, `ram`, `quality`, etc.).
- **Deterministic output:** Typed catalog controls and custom arguments emit identical, safely tokenized argument vectors with zero shell expansion.

---

## 2. Architecture & Data Contracts

### 2.1 Core Models & Catalog Merging
- `ArgumentCatalog`: Catalog version, reference URL/date, categories, and argument definitions.
- `ArgumentDefinition`: Canonical flag, aliases, value schema (`boolean`, `integer`, `number`, `string`, `path`, `enum`, `list`, `json`), category, `isAdvanced`, `commonRank`, help metadata, effects, tags, safety flags.
- `EffectiveArgument`: The catalog definition merged against runtime capability probes and current profile values.
- **Resolution Matrix:**
  - *Catalog + Runtime match:* Rendered as standard active control.
  - *Runtime-only (missing from catalog):* Displayed under "Uncatalogued runtime options" with raw syntax and no speculative effect claims.
  - *Profile value unsupported by runtime:* Retained in profile, marked with an incompatible warning; never dropped.
  - *Profile value missing from catalog:* Moved to Custom Parameters with origin marked as `catalog-mismatch`.
  - *Legacy/deprecated flags:* Mapped to custom passthrough with migration guidance.

### 2.2 Schema & Catalog Sample

Catalogs live in versioned package paths (e.g., `backend/src/llamawebui/catalogs/llama-server-v0.4.1.json`).

```json
{
  "$schema": "[https://json-schema.org/draft/2020-12/schema](https://json-schema.org/draft/2020-12/schema)",
  "catalogId": "llama-server",
  "catalogVersion": "0.4.1",
  "source": {
    "url": "[https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)",
    "retrieved": "2026-09-21"
  },
  "categories": [
    { "id": "context-kv", "label": "Context and KV cache", "sortOrder": 30 },
    { "id": "runtime-devices", "label": "Runtime and devices", "sortOrder": 20 },
    { "id": "security-network", "label": "Security and network", "sortOrder": 80 }
  ],
  "effectTags": {
    "vram": { "label": "VRAM", "color": "violet" },
    "ram": { "label": "RAM", "color": "blue" },
    "quality": { "label": "Quality", "color": "green" },
    "decodespeed": { "label": "Decode", "color": "yellow" },
    "security": { "label": "Security", "color": "pink" }
  },
  "arguments": [
    {
      "id": "ctx-size",
      "flag": "--ctx-size",
      "aliases": ["-c"],
      "label": "Context size",
      "category": "context-kv",
      "isAdvanced": false,
      "commonRank": 20,
      "value": { "type": "integer", "default": 0, "minimum": 0, "unit": "tokens" },
      "help": {
        "summary": "Maximum prompt context per slot.",
        "details": "Larger contexts support longer conversations but increase KV-cache memory.",
        "examples": ["32768", "131072"],
        "effects": [{ "text": "Increases KV-cache memory usage.", "direction": "increase", "tags": ["vram", "ram"] }]
      },
      "effectTags": ["vram", "ram", "quality"],
      "availability": { "requiresAdvertisedFlag": true }
    },
    {
      "id": "cors-origins",
      "flag": "--cors-origins",
      "aliases": [],
      "label": "Allowed CORS origins",
      "category": "security-network",
      "isAdvanced": true,
      "commonRank": null,
      "value": { "type": "list", "separator": ",", "itemType": "string" },
      "help": { "summary": "Allow browser requests from selected origins.", "examples": ["[http://127.0.0.1:5173](http://127.0.0.1:5173)"] },
      "effectTags": ["security"],
      "safety": { "level": "warning", "warning": "Modifies cross-origin native API access.", "requiresConfirmation": true },
      "availability": { "requiresAdvertisedFlag": true }
    }
  ]
}
```

---

## 3. Implementation Requirements

### Backend & API
- **Catalog loader:** JSON Schema validation on boot. Rejects duplicate IDs/flags, invalid categories, or malformed schemas.
- **Probe integration:** Parse runtime `--help` outputs to derive canonical flags, aliases, syntax, and raw help text hashes.
- **Endpoints:**
  - `GET /api/argument-catalog`: Global definitions, categories, and version metadata.
  - `GET /api/runtimes/{id}/argument-catalog`: Effective catalog merged with active runtime capabilities and compatibility issues.
  - Profile read/write endpoints supporting both catalog-backed schemas and backwards-compatible legacy configurations.
- **Validation engine:** Authoritative server-side checks for types, bounds, enums, path boundaries, mutual exclusivity, duplicate emitted flags, deprecated usage, and security warnings (e.g., open CORS, external logging).

### Frontend UI & Interaction
- **Rendering engine:** Dynamic controls for primitives, paths, enums, lists, and JSON based on catalog schemas.
- **Presentation tiers:**
  - *Simple mode:* High-impact safe options (`commonRank` filtered).
  - *Advanced mode:* Full search, category navigation, "only changed" filters, and uncatalogued options.
  - *Custom Parameters:* Editor for arbitrary key-value flags. Blocks shell syntax/redirection, but leaves semantic argument checks to the executable.
- **Help layer:** Keyboard-accessible `?` button on every field. Renders a popover on desktop and a bottom-sheet/modal on mobile (`390x844`), displaying syntax, defaults, examples, notes, and colored direction tags.
- **Launch safeguards:** Pre-flight launch preview displaying the finalized argument vector and active validation blockers.

---

## 4. Delivery Slices

### Slice 1 — Catalog Foundation, Probing & Profile Migration
*Establish data contracts, server validation, runtime probing, and backward-compatible storage.*
- Implement catalog JSON Schema, loader, startup validation, and the bundled v0.4.1 catalog dataset.
- Extend runtime capability probing to capture and hash runtime flags, aliases, and help output.
- Build the effective-catalog service and REST endpoints (`/api/argument-catalog`, `/api/runtimes/{id}/argument-catalog`).
- Implement the structured profile migration engine: convert legacy profiles to the new structured format, preserve unknown/removed flags as Custom Parameters, and verify deterministic vector serialization.

### Slice 2 — Dynamic Editor & Accessible Help System
*Deliver the primary user-facing dynamic UI for common profile configurations.*
- Build dynamic form controls (boolean, numeric, text, enum, list, path, JSON) driven by catalog definitions.
- Implement Simple Mode rendering using `commonRank` and category groupings, with fallback retention to the legacy UI on loader failure.
- Implement the accessible `?` help affordance with desktop popovers and mobile bottom-sheet/modal layouts (`390x844`).
- Render directional effect indicators, colored effect-tag pills, compatibility callouts, and safety warnings.

### Slice 3 — Advanced Controls, Custom Fallbacks & Launch Hardening
*Complete full argument surface coverage, safety checks, and production rollout.*
- Deliver Advanced Mode with category filters, full-text search, and "only changed" filters.
- Build the "Uncatalogued runtime options" and "Custom Parameters" editors with shell-injection guards and import migration tracking (`catalog-mismatch`).
- Implement comprehensive client and server pre-flight validation (conflicts, bounds, missing dependencies, security acknowledgements).
- Add launch preview inspection, runtime-switch compatibility warnings, and finalize cutover by deprecating legacy hardcoded forms.

---

## 5. Verification & Acceptance Criteria

- **Catalog coverage:** All process arguments in the v0.4.1 CLI reference are modeled; API-only request fields are strictly excluded.
- **Runtime adaptation:** Advertised executable flags correctly gate UI control availability; missing or mismatched options route to custom fallback fields without data loss.
- **Profile integrity:** Migrated profiles round-trip with zero dropped keys and generate byte-for-byte identical launch vectors.
- **Safety enforcement:** Malformed inputs, conflicting flags, and unconfirmed security options trigger explicit errors/warnings; raw command injection is impossible.
- **Responsive accessibility:** Full keyboard navigation, screen reader accessibility, and error-free layouts verified across desktop and mobile (`390x844`).