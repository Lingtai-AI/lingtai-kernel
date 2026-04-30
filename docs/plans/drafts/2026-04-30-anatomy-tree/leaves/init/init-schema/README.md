# Init Schema Validation

> **Subsystem:** init / init-schema
> **Layer:** Agent initialization

---

## What

Every agent boots from `init.json`. Before startup, `validate_init()` checks required fields, types, and cross-field constraints. A `ValueError` aborts startup; unknown fields produce warnings (not errors).

---

## Contract

### Required top-level fields

- `manifest` (dict) — required
- Five text pairs (inline `_file`): `principle`, `covenant`, `pad`, `prompt`, `soul` — at least one of each pair required
- Three optional pairs: `procedures`, `brief`, `comment`

### Required manifest fields

- `manifest.llm` (dict) with `provider` (str) and `model` (str)

### Conditional requirements

- If `manifest.llm.api_key_env` set and `api_key` absent → top-level `env_file` required

### Preset validation (when `manifest.preset` present)

- `active` (str), `default` (str), `allowed` (list[str], non-empty) — all required
- Both `active` and `default` must appear in `allowed`
- Unknown keys in `preset` → warning

### Error vs Warning

| Condition | Outcome |
|-----------|---------|
| Missing required / wrong type on known field | `ValueError` → abort |
| Unknown top-level key | Warning (logged, startup continues) |
| Unknown manifest key | Warning |
| `bool` in numeric field | `ValueError` (bool is subclass of int) |

---

## Source

All references to `lingtai-kernel/src/lingtai/`.

| What | File | Line(s) |
|------|------|---------|
| `validate_init()` | `init_schema.py` | 59-227 |
| `TOP_KNOWN` / `TOP_OPTIONAL` | `init_schema.py` | 13-31 |
| `MANIFEST_REQUIRED` / `MANIFEST_OPTIONAL` | `init_schema.py` | 33-56 |
| Text-pair validation | `init_schema.py` | 72-81 |
| Preset validation | `init_schema.py` | 114-170 |
| LLM subfield validation | `init_schema.py` | 182-199 |
| `api_key_env` → `env_file` cross-check | `init_schema.py` | 194-199 |
| Bool-reject for numerics | `init_schema.py` | 265-266 |
| Called from `_read_init()` | `agent.py` | 583, 614-620 |
| Called from `cli.py` | `cli.py` | 14, 49 |

---

## Related

| Sibling leaf | Relationship |
|--------------|-------------|
| `init/init-structure` | Overall init.json shape and file-path resolution |
| `init/preset-materialization` | Preset expansion happens before validation |
| `core/molt-protocol` | `molt_pressure`/`molt_prompt` validated here as manifest optionals |
