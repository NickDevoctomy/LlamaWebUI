# Dynamic `llama-server` Argument UI Plan

**Status:** Proposed, separate from the main implementation plan  
**Plan date:** 2026-09-21  
**Reference:** [`docs/llama-server-v0.4.1-cli-reference.md`](../llama-server-v0.4.1-cli-reference.md)  
**Scope:** Dynamically describe, validate, render, explain, and persist the complete `llama-server` launch-argument surface.

## 1. Goal

Move profile configuration from a fixed collection of hand-coded controls to a data-driven argument system. A versioned JSON catalog should describe every known `llama-server` argument, while the selected runtime's probed `--help` output remains the compatibility source of truth.

The UI should:

- Show common, high-value settings first in a simple view.
- Organize all supported settings into meaningful categories.
- Make advanced settings available without overwhelming new users.
- Preserve newly introduced or runtime-specific options through a safe custom-argument workflow.
- Provide a small `?` help affordance beside each argument on desktop and mobile.
- Open an accessible help popover/dialog containing the description, syntax, defaults, examples, compatibility notes, effects, and coloured effect tags.
- Generate the same validated argument vector and model preset regardless of whether a value came from a typed control or a custom argument.

This plan does **not** make arbitrary command execution possible. The control plane must continue to launch `llama-server` using a validated argument vector and registered executable only.

## 2. Findings from the v0.4.1 reference

The reference contains a broad surface spanning CPU, context/KV cache, attention/RoPE, device placement, model loading, multimodal input, sampling, routing/API/security, logging, speculative decoding, and removed legacy arguments.

The current application has first-class controls for a small subset, including `--model`, `--models-preset`, host/port, API-key-file, `--n-gpu-layers`, `--ctx-size`, `--flash-attn`, `--load-mode`, `--lazy-mode`, `--cache-ram`, `--fit`, tensor overrides, K/V cache types, threads, batch sizes, and reasoning preservation. It also has an advanced escape hatch, but that is not yet a discoverable, catalog-driven experience.

The reference already supplies useful metadata that should become structured data rather than remaining only in tables:

- Canonical argument and aliases.
- Value syntax and enum choices.
- Description and effect summary.
- Effect tags such as `vram`, `ram`, `quality`, `security`, `prefillspeed`, and `decodespeed`.
- Existing first-class support status.
- Category boundaries.
- Runtime/build-dependent availability.

The catalog must distinguish process arguments from HTTP request fields. Request-level sampling or completion parameters must not accidentally be persisted as router startup arguments.

## 3. Proposed architecture

### 3.1 Static catalog plus dynamic runtime capabilities

Use two complementary sources:

1. **Versioned application catalog JSON**: human-curated descriptions, categories, presentation priority, risk annotations, effect tags, input schemas, examples, and migration aliases.
2. **Runtime capability probe**: the selected executable's parsed `--help` output, existing in `RuntimeCapabilities`, and build/version metadata.

The effective catalog is the intersection/merge of these sources:

- A catalog entry is **available** only when the runtime advertises its canonical name or an alias, unless the user is explicitly editing a deferred custom argument.
- A runtime-advertised argument absent from the catalog appears in an **Uncatalogued runtime options** section with conservative generic help and no invented effect claims.
- A catalog entry missing from the runtime is retained in the profile but marked **unsupported by selected runtime**, so changing runtimes does not silently discard configuration.
- A profile argument missing from the selected catalog is automatically represented in **Custom Parameters** with its original flag/value preserved. It remains editable and launchable as a pass-through custom entry instead of blocking profile editing.
- Removed/legacy arguments remain visible through Custom Parameters when imported from an old profile or command, with a migration note. The app does not interpret their semantics; `llama-server` decides whether the selected runtime accepts them.

The catalog should be loaded at application startup, validated against a JSON Schema, and exposed through a read-only API with a catalog version/hash. Runtime-specific availability is computed server-side.

### 3.2 Domain model

Introduce typed domain objects along these lines:

- `ArgumentCatalog` — catalog version, source reference, categories, and entries.
- `ArgumentDefinition` — canonical name, aliases, value schema, category, advanced flag, priority, help, effects, safety, and compatibility metadata.
- `ArgumentValue` — typed persisted value plus source (`default`, `user`, `imported`, or `custom`).
- `EffectiveArgument` — definition merged with selected-runtime availability and current profile value.
- `ArgumentValidationIssue` — unsupported, invalid type/range/enum, conflicting, unsafe, or deprecated state.

Persist profile values as structured argument records rather than relying on UI-specific field names. Keep a migration path from the current typed `configuration` and `advanced` records. Existing profiles must render identically after migration.

### 3.3 Categories

Use stable category IDs, display labels, ordering, and descriptions. Initial categories should cover the reference's sections and split high-frequency settings where useful:

- Model and files
- Runtime and devices
- CPU and NUMA
- Context and KV cache
- Batching and concurrency
- Attention and RoPE
- Loading and memory
- Multimodal
- Sampling and output
- Reasoning and chat templates
- Speculative decoding
- Server, routing, and API
- Security and network
- Logging and diagnostics
- Experimental and compatibility

`Category` is the canonical property name. Do not use the misspelling `Caregory` in the schema or API.

### 3.4 Simple and advanced presentation

Every catalog entry should include `isAdvanced` and `commonRank` (or an equivalent explicit priority):

- **Simple mode**: show only the most common safe settings, grouped into a few beginner-friendly sections. Examples include model path, alias, context size, GPU layers, flash attention, threads, batch sizes, cache types, load mode, and host/port where applicable.
- **Advanced mode**: show all catalogued settings supported by the runtime, with search, category filters, and an “only changed” filter.
- **Custom/runtime options**: show runtime-advertised entries not yet curated by the catalog and a controlled custom-entry editor for import/migration cases.
- **Security-sensitive options**: require explicit warnings and confirmation where a setting exposes a network, secret, filesystem, prompt, tool, or child-process surface.
- **Experimental options**: remain hidden from simple mode and carry an `experimental` badge.

The mode changes presentation only; it must not change validation or what can be persisted.

## 4. JSON catalog contract

Store the catalog as a versioned, checked-in file bundled with the Python application, for example `backend/src/llamawebui/catalogs/llama-server-v0.4.1.json`. The runtime version belongs in the filename and catalog metadata. When a new runtime reference is adopted, add a new versioned catalog file rather than overwriting the previous one. Keep descriptions and examples synchronized with the corresponding CLI reference and include the upstream reference URL/date.

Recommended field conventions:

- `id`: stable internal identifier, normally the canonical long option without leading dashes.
- `flag`: canonical emitted option.
- `aliases`: short and legacy spellings accepted during import/probing.
- `value`: `boolean`, `integer`, `number`, `string`, `path`, `enum`, `list`, or `json` with constraints.
- `isAdvanced`: controls simple/advanced presentation; it is not a security boundary.
- `category`: stable category ID.
- `commonRank`: lower values appear earlier; `null` means not common.
- `effects`: short, directional statements shown in help.
- `effectTags`: controlled tag IDs rendered as coloured pills.
- `availability`: optional minimum version/build, backend constraints, and deprecation metadata.
- `safety`: warnings and confirmation requirements.

### 4.1 Sample JSON for review

The following is an intentionally small sample, not the complete catalog. It demonstrates simple and advanced arguments, categories, typed values, aliases, help content, effects, coloured tags, and runtime constraints.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "catalogId": "llama-server",
  "catalogVersion": "0.4.1",
  "source": {
    "url": "https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md",
    "retrieved": "2026-09-21"
  },
  "categories": [
    {
      "id": "context-kv",
      "label": "Context and KV cache",
      "description": "Context length, cache placement, and memory trade-offs.",
      "sortOrder": 30
    },
    {
      "id": "runtime-devices",
      "label": "Runtime and devices",
      "description": "CPU/GPU placement and backend-specific execution settings.",
      "sortOrder": 20
    },
    {
      "id": "security-network",
      "label": "Security and network",
      "description": "Network exposure and browser access controls.",
      "sortOrder": 80
    }
  ],
  "effectTags": {
    "vram": { "label": "VRAM", "color": "violet" },
    "ram": { "label": "RAM", "color": "blue" },
    "quality": { "label": "Quality", "color": "green" },
    "prefillspeed": { "label": "Prefill", "color": "orange" },
    "decodespeed": { "label": "Decode", "color": "yellow" },
    "reliability": { "label": "Reliability", "color": "red" },
    "backend": { "label": "Backend", "color": "cyan" },
    "security": { "label": "Security", "color": "pink" },
    "network": { "label": "Network", "color": "indigo" }
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
      "value": {
        "type": "integer",
        "default": 0,
        "minimum": 0,
        "unit": "tokens",
        "help": "0 loads the model's advertised context size."
      },
      "help": {
        "summary": "Maximum prompt context per slot.",
        "details": "Larger contexts support longer conversations but increase KV-cache memory and can reduce concurrency.",
        "examples": ["32768", "131072"],
        "effects": [
          { "text": "Higher values consume more KV-cache memory.", "direction": "increase", "tags": ["vram", "ram"] },
          { "text": "Longer prompts remain available.", "direction": "increase", "tags": ["quality"] }
        ]
      },
      "effectTags": ["vram", "ram", "quality", "reliability"],
      "availability": { "requiresAdvertisedFlag": true }
    },
    {
      "id": "n-gpu-layers",
      "flag": "--n-gpu-layers",
      "aliases": ["-ngl", "--gpu-layers"],
      "label": "GPU layers",
      "category": "runtime-devices",
      "isAdvanced": false,
      "commonRank": 10,
      "value": {
        "type": "integer-or-enum",
        "enum": ["auto", "all"],
        "minimum": 0,
        "unit": "layers"
      },
      "help": {
        "summary": "Choose how many model layers are stored on the accelerator.",
        "details": "More layers usually improve speed but require more VRAM. Use auto/all only when the selected backend supports them.",
        "examples": ["0", "60", "all"],
        "effects": [
          { "text": "More offloaded layers use more VRAM.", "direction": "increase", "tags": ["vram"] },
          { "text": "More offloaded layers can improve decode speed.", "direction": "increase", "tags": ["decodespeed"] }
        ]
      },
      "effectTags": ["vram", "decodespeed", "backend"],
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
      "value": {
        "type": "list",
        "separator": ",",
        "itemType": "string",
        "placeholder": "https://app.example"
      },
      "help": {
        "summary": "Allow browser requests from selected origins.",
        "details": "A broad origin such as * increases exposure. Prefer exact origins and review this setting whenever the server is not loopback-only.",
        "examples": ["http://127.0.0.1:5173", "https://operator.example"]
      },
      "effectTags": ["security", "network"],
      "safety": {
        "level": "warning",
        "warning": "This changes cross-origin access to the native API.",
        "requiresConfirmation": true
      },
      "availability": { "requiresAdvertisedFlag": true }
    }
  ]
}
```

The full catalog should include every current argument in the reference, including unsupported first-class settings, while excluding API request-only fields. The sample is intentionally small but self-validating: every referenced category and effect tag is declared. The eventual checked-in catalog should be validated in CI so documentation examples cannot drift from the JSON Schema.

## 5. Backend/API work

1. Add JSON Schema validation and a catalog loader with clear startup errors for duplicate IDs, duplicate flags, invalid categories/tags, invalid value schemas, and malformed availability rules. Bundle versioned catalog files in the Python package and select them explicitly by runtime/reference version.
2. Parse the current reference into the first complete catalog, preserving source/version information and an explicit support/curation status.
3. Extend runtime probing to retain normalized advertised flags, aliases, value syntax, and raw help text hash.
4. Add an effective-catalog service that merges catalog definitions with runtime availability and profile values.
5. Add read-only endpoints:
   - `GET /api/argument-catalog` for categories, definitions, and catalog metadata.
   - `GET /api/runtimes/{id}/argument-catalog` for runtime availability and compatibility issues.
6. Add typed profile endpoints for structured argument values while accepting the existing profile format during migration.
7. Validate catalog-backed values server-side: types, ranges, enums, list syntax, path containment, conflicts, security warnings, runtime support, and duplicate emitted flags.
8. Update preset/argument rendering so catalog-backed values and custom entries share one canonical serializer and deterministic ordering. Custom entries are intentionally pass-through UI conveniences; the app does not validate their flag/value semantics and `llama-server` remains responsible for accepting or rejecting them.
9. Preserve unsupported values on runtime switch and surface them as compatibility issues; never silently drop them. Offer the user the available bundled catalog versions even when none exactly matches the selected runtime, with a clear compatibility warning.
10. During catalog loading or profile migration, move any profile argument absent from the selected catalog into the Custom Parameters collection, preserving its original token/value representation and marking its provenance as `catalog-mismatch`.
11. Add import/export support for the catalog-backed profile format, including the selected catalog version and custom parameters with their provenance.

## 6. Frontend work

1. Fetch the effective catalog with TanStack Query and render categories/controls from metadata.
2. Build a control renderer for booleans, numbers, enums, strings, paths, lists, JSON, and repeated values.
3. Add Simple/Advanced mode with a clear mode switch and a count of hidden advanced or unsupported settings.
4. Add category navigation, search, changed-only filtering, and a runtime-unsupported filter.
5. Add a `?` button beside every argument label. It must be keyboard-focusable, have an accessible name, and work on touch screens.
6. Use a popover on desktop and a modal/bottom sheet on narrow screens. Include:
   - Argument name and aliases.
   - Short description and expanded details.
   - Default, current value, accepted values, and example.
   - Effect statements with direction indicators.
   - Coloured pill tags using the controlled effect-tag palette.
   - Backend/version/compatibility notes.
   - Security or experimental warnings.
7. Add an explicit Custom Parameters editor for arbitrary flag/value pairs such as `--custom-thing 100`. Do not interpret or validate the semantics of these entries; preserve them as argument-vector tokens and let `llama-server` decide whether they are valid. Still reject shell syntax, command strings, executable paths, or any form of shell expansion.
8. Show a generated launch preview and validation issues before save/apply.
9. Keep the current profile workflow usable during rollout, including existing exports and old profile records.
10. Verify desktop and `390x844` layouts: no horizontal overflow, no tooltip clipped behind navigation, readable long values, and usable help dialogs.

## 7. Validation and conflict policy

Validation should occur at catalog load, API request, save/import, runtime selection, and immediately before launch.

At minimum, detect:

- Unsupported catalog flags for the selected executable.
- Invalid enum, numeric, boolean, JSON, list, or path values.
- Mutually exclusive options such as positive/negative forms.
- Duplicate aliases or emitted flags.
- Model/profile settings incorrectly placed in router-global settings.
- Security-sensitive network/API/tool/MCP/prompt logging settings.
- Deprecated or removed flags.
- Backend-specific options selected on an incompatible runtime.
- Values that exceed the selected model/runtime's known capabilities where the app can determine that safely.

Use severity levels:

- **Error:** catalog-backed setting cannot be saved/applied/launched safely.
- **Warning:** can save and may be applied, but requires acknowledgement or is likely to reduce reliability/security; catalog/runtime version mismatch belongs here unless the user chooses a stricter policy.
- **Info:** explanatory effect or migration note.

## 8. Delivery slices

### Slice 1 — Contract and catalog foundation

- Define JSON Schema and typed loader.
- Add a small self-validating catalog fixture and loader tests.
- Add effective-catalog API with categories and runtime availability.
- No UI replacement yet.

### Slice 2 — Complete v0.4.1 catalog

- Convert every process argument in the reference into catalog entries.
- Add category, `isAdvanced`, common priority, value schema, help, effects, tags, safety, and compatibility metadata.
- Add a catalog/reference consistency checklist and duplicate/missing-entry tests.

### Slice 3 — Structured profile migration

- Migrate current typed configuration and advanced options into structured values.
- Preserve export/import compatibility.
- Add deterministic serialization and runtime-switch preservation tests.

### Slice 4 — Dynamic simple editor

- Render common controls from the catalog.
- Replace the current fixed basic controls for the migrated subset.
- Retain the current editor as a fallback if catalog loading fails.

### Slice 5 — Help experience and effect tags

- Add accessible `?` affordances and responsive popover/modal help.
- Implement coloured tag pills, effect direction indicators, warnings, and examples.
- Add component and browser acceptance coverage.

### Slice 6 — Advanced, custom, and uncatalogued options

- Add advanced search/category filters and custom/runtime-advertised options.
- Add import migration handling for unknown/deprecated flags.
- Ensure no arbitrary shell or executable input is introduced.

### Slice 7 — Launch hardening and rollout

- Apply final validation at start/restart.
- Add launch preview, conflict resolution, and unsupported-runtime UX.
- Update operator documentation, migration notes, and implementation progress.
- Remove duplicated fixed-control paths only after equivalent dynamic coverage is proven.

## 9. Testing strategy

### Backend

- JSON Schema and loader validation tests.
- Catalog completeness and uniqueness tests.
- Runtime catalog merge tests for advertised, missing, aliased, unknown, and deprecated options.
- Profile migration round-trip tests.
- Typed value and conflict validation tests.
- Deterministic argument-vector and preset serialization tests.
- Security tests proving no raw secrets are returned in catalog/profile responses and no shell command is constructed.

### Frontend

- Renderer tests for each input type and category/filter behavior.
- Help popup keyboard, focus, close, and touch behavior.
- Tag colour/direction rendering and warning states.
- Simple/Advanced mode and unsupported-option states.
- Profile save/import/export round trips.
- Responsive browser checks at desktop and `390x844`.

### Manual acceptance

1. Open a profile in Simple mode and confirm common settings render from catalog data.
2. Switch to Advanced mode and locate an option from each major category.
3. Open `?` help with keyboard and touch; verify details, examples, effect statements, and coloured pills.
4. Select an option unsupported by the active runtime, choose an available bundled catalog version, and confirm the mismatch is clearly shown while the setting remains editable.
5. Open a profile containing an argument absent from the selected catalog and confirm it appears in Custom Parameters with its original value and a catalog-mismatch note.
6. Add `--custom-thing 100` in Custom Parameters and confirm it is preserved in the launch preview without app-side semantic validation.
7. Import an old profile/command containing an unknown option and confirm it is preserved as a custom/unresolved entry rather than discarded.
8. Apply a valid profile and confirm the launched argument vector contains no duplicate or shell-expanded arguments.
9. Switch runtimes and confirm incompatible values remain visible with actionable compatibility messages.


## 10. Definition of done

- Every process argument in the v0.4.1 reference has a catalog entry or an explicit documented exclusion.
- The catalog validates and exposes stable categories, `isAdvanced`, common priority, typed values, help, effects, tags, and safety metadata.
- The selected runtime's advertised flags determine actual availability.
- Existing profiles migrate without losing values and produce equivalent launch output.
- Simple, Advanced, custom, and unsupported-option workflows are complete.
- Every rendered argument has accessible help with responsive effect-tag pills.
- Server-side validation remains authoritative and launches only safe argument vectors.
- Backend/frontend quality gates, responsive browser acceptance, and documentation updates pass.
