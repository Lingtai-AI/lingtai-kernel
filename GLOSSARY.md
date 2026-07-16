---
name: glossary-of-glossaries
schema_version: 1
related_files:
  - ANATOMY.md
  - CONTRACT.md
  - docs.yaml
  - src/lingtai/kernel/tool_glossary.py
  - src/lingtai/tools/glossary_validator.py
  - tests/test_tool_glossary.py
maintenance: |
  This file is the repository-root glossary governance document: the glossary
  of glossaries / 词汇表之词汇表 for distributed tool-glossary resources. Keep
  it aligned with src/lingtai/kernel/tool_glossary.py and tests/test_tool_glossary.py
  whenever glossary frontmatter schema, body policy, language ownership, or
  templates change.
---
# Glossary of Glossaries / 词汇表之词汇表

## Purpose

A tool glossary is model-facing alias and localized-name help. It helps the
model recognize a tool's stable English names in another language. It is not
human UI, not schema, not a manual, not a Contract, and not Anatomy.

Canonical tool names, action names, parameter names, enum values, and other
code identifiers stay English. Glossaries may explain the intended local name
or cultural metaphor, but they must not create localized aliases accepted by
the runtime.

## Distributed resources

Each first-party tool package owns exactly three resources:

- `glossary-en.md`
- `glossary-zh.md`
- `glossary-wen.md`

Each resource uses the current six-field frontmatter schema:

```yaml
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.<package>
language: <en|zh|wen>
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
maintenance: |
  <package/language-specific maintenance sentence>
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
```

English glossary bodies are empty. `zh` and `wen` bodies are non-empty and
distinct. Body content must be a minimal term mapping plus at most one or two
sentences of naming or cultural rationale.

Glossary bodies must not translate or duplicate tool schema, parameters, action
behavior, operational warnings, examples, manuals, Contracts, or Anatomy. Those
sources remain authoritative in their own layers. Maintain a glossary when a
tool name, local alias, or naming/cultural rationale changes, not whenever the
public schema changes.

The next glossary-governance PR is expected to repair non-daemon oversized
bodies. This document defines the policy now; it does not claim every
non-daemon body already satisfies the minimal-body convention.

## Copy-ready templates

### English

```markdown
---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.<package>
language: en
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/<package>/glossary-zh.md
- src/lingtai/tools/<package>/glossary-wen.md
maintenance: |
  English glossary for the `<package>` tool package (lingtai.tools.<package>); the English body must stay empty per tool_glossary.py's language contract — update only when glossary identity, aliases, or naming rationale change.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
```

### Simplified Chinese (`zh`)

```markdown
---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.<package>
language: zh
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/<package>/glossary-en.md
- src/lingtai/tools/<package>/glossary-wen.md
maintenance: |
  Simplified-Chinese (zh) glossary for the `<package>` tool package (lingtai.tools.<package>); update when names, aliases, or naming rationale change.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**术语对照**

- `<tool>`：<local name>。<one or two concise naming-rationale sentences>
- `<action>`：<local alias>
```

### Classical Chinese (`wen`)

```markdown
---
kind: tool-glossary
schema_version: 1
tool_package: lingtai.tools.<package>
language: wen
related_files:
- docs.yaml
- src/lingtai/kernel/tool_glossary.py
- src/lingtai/tools/glossary_validator.py
- src/lingtai/tools/<package>/glossary-en.md
- src/lingtai/tools/<package>/glossary-zh.md
maintenance: |
  Classical-Chinese (wen) glossary for the `<package>` tool package (lingtai.tools.<package>); update when names, aliases, or naming rationale change.
  Body policy: maintain only a minimal term mapping plus at most one or two sentences of naming rationale; do not translate or duplicate the tool schema, parameters, action behavior, manual, contract, or anatomy.
---
**名相对照**

- `<tool>`：<local name>。<one or two concise naming-rationale sentences>
- `<action>`：<local alias>
```

## Review checklist

- The file uses the exact six frontmatter keys and `schema_version: 1`.
- `maintenance` contains the canonical body-policy sentence from
  `src/lingtai/kernel/tool_glossary.py`.
- English body is empty.
- `zh` and `wen` bodies are non-empty, distinct, and minimal.
- The body maps terms and gives at most one or two naming-rationale sentences.
- The body does not duplicate schema, parameters, behavior, examples, manuals,
  Contracts, or Anatomy.
- The change is motivated by names, aliases, or cultural rationale, not by an
  unrelated public-schema edit.
