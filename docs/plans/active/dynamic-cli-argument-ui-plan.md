# Dynamic `llama-server` Argument UI Plan

**Status:** Implemented (Slices 1–3), awaiting maintainer review. See §6 for implementation notes  
**Plan date:** 2026-09-21  
**Reference:** [`docs/llama-server-v0.4.1-cli-reference.md`](../llama-server-v0.4.1-cli-reference.md)  
**Scope:** Dynamically describe, validate, render, explain, and persist the complete `llama-server` launch-argument surface.

---

## 1. Goals & Principles

Transition profile configuration from static, hand-coded fields to a data-driven catalog system while keeping the control plane strictly isolated from arbitrary command execution.

- **Dual-source truth:** A versioned JSON catalog provides metadata (descriptions, schemas, categories, effect tags), while the selected runtime's probed `--help` output informs compatibility. Probe results are advisory: a flag missing from `--help` produces a warning, never a refusal, because help text can be incomplete.
- **Progressive disclosure:** Safe, high-frequency arguments appear in a default Simple mode; every other catalogued and uncatalogued option is reached through Advanced search (there is no "show all" list), and custom overrides live in Custom Parameters.
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
  - *Profile value not advertised by runtime:* Retained in profile and still emitted at launch, marked with a compatibility warning; never dropped or blocked. If `--help` cannot be parsed or is empty, runtime compatibility checks are skipped.
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
- **Validation engine:** Authoritative server-side checks for types, bounds, enums, path boundaries, mutual exclusivity, duplicate emitted flags, deprecated usage, and security warnings (e.g., open CORS, external logging). Runtime-advertisement mismatches are warnings only; launch blockers are limited to definite faults (invalid type/bounds, shell syntax or newlines, duplicate flags, missing model file, unconfirmed security options).

### Frontend UI & Interaction
- **Rendering engine:** Dynamic controls for primitives, paths, enums, lists, and JSON based on catalog schemas.
- **Presentation tiers:**
  - *Simple mode:* The options the current UI already exposes, flagged `isAdvanced: false` in the catalog and ordered by `commonRank`.
  - *Advanced search:* A search box matching argument name/flag and description text. Selecting a result adds it to the form; any argument with a value is automatically pinned and always shown. There is no "show all" view; uncatalogued runtime options are found through the same search.
  - *Custom Parameters:* Editor for arbitrary key-value flags. Blocks shell syntax/redirection, but leaves semantic argument checks to the executable.
- **Help layer:** Keyboard-accessible `?` button on every field. Renders a popover on desktop and a bottom-sheet/modal on mobile (`390x844`), displaying syntax, defaults, examples, notes, and colored direction tags.
- **Launch safeguards:** Pre-flight launch preview displaying the finalized argument vector and active validation blockers.

---

## 4. Delivery Slices

### Slice 1 — Catalog Foundation, Probing & Profile Migration
*Establish data contracts, server validation, runtime probing, and backward-compatible storage.*
- Implement catalog JSON Schema, loader, startup validation, and the bundled v0.4.1 catalog dataset.
- Extend runtime capability probing to capture and hash runtime flags, aliases, and help output.
- Build the effective-catalog service and REST endpoints (`/api/argument-catalog`, `/api/runtimes/{id}/argument-catalog`), reporting runtime mismatches as warnings; this replaces the current hard error in `validate_profile` (`domain/model_profile.py`) for flags not advertised by `--help`.
- Implement the structured profile migration engine: convert legacy profiles to the new structured format, preserve unknown/removed flags as Custom Parameters, and verify deterministic vector serialization.

### Slice 2 — Dynamic Editor & Accessible Help System
*Deliver the primary user-facing dynamic UI for common profile configurations.*
- Build dynamic form controls (boolean, numeric, text, enum, list, path, JSON) driven by catalog definitions.
- Implement Simple Mode rendering for the options the current UI exposes (`isAdvanced: false`, ordered by `commonRank`, grouped by category), with fallback retention to the legacy UI on loader failure.
- Implement advanced-option search (simple text match on name/flag and description); arguments with a set value are pinned and always shown, and there is no "show all" view.
- Implement the accessible `?` help affordance with desktop popovers and mobile bottom-sheet/modal layouts (`390x844`).
- Render directional effect indicators, colored effect-tag pills, compatibility callouts, and safety warnings.

### Slice 3 — Advanced Controls, Custom Fallbacks & Launch Hardening
*Complete full argument surface coverage, safety checks, and production rollout.*
- Build the "Uncatalogued runtime options" and "Custom Parameters" editors with shell-injection guards and import migration tracking (`catalog-mismatch`).
- Implement comprehensive client and server pre-flight validation (conflicts, bounds, missing dependencies, security acknowledgements).
- Add launch preview inspection, runtime-switch compatibility warnings, and finalize cutover by deprecating legacy hardcoded forms.

---

## 5. Verification & Acceptance Criteria

- **Catalog coverage:** All process arguments in the v0.4.1 CLI reference are modeled; API-only request fields are strictly excluded.
- **Runtime adaptation:** Advertised executable flags inform UI control availability; missing or mismatched options route to custom fallback fields without data loss, and a profile using a flag absent from `--help` still saves and launches with a warning.
- **Profile integrity:** Migrated profiles round-trip with zero dropped keys and generate byte-for-byte identical launch vectors.
- **Safety enforcement:** Malformed inputs, conflicting flags, and unconfirmed security options trigger explicit errors/warnings; raw command injection is impossible.
- **Responsive accessibility:** Full keyboard navigation, screen reader accessibility, and error-free layouts verified across desktop and mobile (`390x844`).
---

## 6. Implementation Notes

**Catalog and contract**
- Bundled catalog: `backend/src/llamawebui/catalogs/llama-server-v0.4.1.json` (214 arguments). That is every row of the v0.4.1 reference plus `--cache-type-k/v`, which the reference lists only in its summary.
- The data contract is the pydantic model set in `domain/argument_catalog.py`. `argument-catalog.schema.json` is generated from it, and a test keeps the two identical. The catalog is loaded and validated before startup touches any state, so an invalid catalog stops the app. The loader rejects duplicate ids or flags (aliases and negations included), unknown categories or effect tags, dangling `conflicts`/`requires`/`replacement` references, and malformed value schemas.
- Besides `editable`, `control` can be `managed` (LlamaWebUI sets it: model, preset, host, port, keys, alias, router-wide `--models-*`, runtime model downloads), `informational` (print-and-exit options) or `removed`.
- A `--help` row parser (`domain/runtime_capabilities.py`) records each option's canonical flag, aliases, syntax, help text and default. These are stored per runtime with a `flags_sha256` fingerprint (Alembic `0014_runtime_option_details`). Runtimes registered earlier need a re-probe to show per-option help.

**Profile storage and migration**
- Configurations are stored as `schema_version: 2`: an ordered `arguments` list of `{id, value}` catalog entries or `{flag, value, origin}` raw entries (`runtime`, `custom`, `catalog-mismatch`, `deprecated`), plus `acknowledged`.
- Legacy flat configurations are converted at startup without changing the stored preset. A legacy option becomes a typed argument only when it emits byte-identical text; otherwise it is kept raw. The original document is kept under `legacy_configuration` until the next edit.
- The API still accepts the legacy flat fields, and imports accept `llamawebui-profile-v1` documents. Exports are `llamawebui-profile-v2`.

**Validation**
- Launch blockers: invalid types, bounds, enum values, patterns or JSON; newlines or control characters; shell syntax in raw values; duplicate flags; a missing or incomplete model; unacknowledged security options; flags LlamaWebUI manages or that only print and exit. The router-start pre-flight re-checks every enabled profile.
- Warnings only: flags the runtime does not advertise, deprecated or removed flags, conflicts, missing dependencies, relative or missing paths, and open CORS.
- Shell-syntax blocking applies only to raw values and targets substitution and whitespace-delimited operators. Legitimate values such as `top_k;top_p` or regexes with `|` are allowed.

**Display and API**
- Sensitive values (`--hf-token`, `--mcp-servers-json`) are masked in logs, command views, the launch preview and the preset shown in the UI. The preset file and profile export keep the real value.
- New endpoints: `GET /api/argument-catalog`, `GET /api/runtimes/{id}/argument-catalog` and `POST /api/profiles/preview`.

**Editor**
- The profile editor has Basic, Arguments and Launch preview tabs. Arguments contains Common settings (the six fields the earlier UI exposed), Advanced search, uncatalogued runtime options and custom parameters.
- The previous fixed form remains only as a fallback for when the catalog cannot load. It preserves the arguments it does not edit.

**Out of scope**
- The profile list's row actions still overflow at 390px. This is the deferred `docs/tech-debt/profile-panel-responsive-layout.md`, not the argument editor.
