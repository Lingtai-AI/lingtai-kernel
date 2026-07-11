"""Characterization suite for the LLM conversation *input* surface.

This package pins the behavior of the two inputs the kernel ``ChatSession`` ABC
declares — ``send(str)`` and ``send(list[ToolResultBlock])`` — against every
*selectable production return regime*, using a mocked transport (no network).

Three things are made executable (see ``regimes.py``):

* **Registry matrix** — every registered provider name is built through the real
  ``LLMService`` end to end (``LLMService(provider=<exact name>, ...)`` ->
  registered factory -> ``create_session``), with the SDK client mocked, and the
  returned adapter/session/``_GatedSession`` **class** is asserted. The mapping is
  therefore the data under test; it cannot drift away from what the factories do,
  and rebinding a provider to a different factory fails the matrix. The union of
  built provider names equals the registry key set. Rows that carry a Responses
  **mode** also assert the built session's ``_stateless_replay`` bit, so fresh-main
  #861's official/stateful (``openai.responses``) and custom/OpenAI-compatible
  stateless (``custom.responses.stateless``) Responses regimes are distinct
  class-plus-mode rows — and their divergent wires (delta + ``previous_response_id``
  vs full replay + no resume id) are proven through the same real ``LLMService``
  route. This is the one class-plus-mode distinction; every other regime is a
  distinct session class.

* **Custom-family schema cross-product** — for ``custom`` and the aliases
  ``grok`` / ``qwen`` / ``kimi`` across ``api_compat`` x ``wire_api``, schema
  *selectability* (``init_schema.validate_init``) is checked *separately* from the
  concrete adapter/session class the accepted configuration builds through the
  real ``LLMService`` path (or the exact factory ``ValueError``). Non-``auto``
  ``wire_api`` is schema-valid only for ``openai`` and ``custom`` +
  ``api_compat=openai``; the alias non-``auto`` rows are rejected.

* **Behavior regimes** — the concrete ``ChatSession`` classes with distinct
  common-input wire behavior (including the DeepSeek / MiMo / Zhipu subclasses
  that override ``_build_messages``, Codex's own REST machinery, and a
  ``_GatedSession``-wrapped session) are each driven through both inputs; the
  tests assert the exact provider wire AND the returned ``LLMResponse`` +
  concrete ``UsageMetadata``.

It is deliberately NOT a governed component: it adds no ``CONTRACT.md``, links
nothing from the root contract, and claims no Ports & Adapters migration. It is a
prerequisite characterization/correction layer whose job is to make the real
per-regime input behavior explicit and executable, so a *future* child contract
can know which concrete providers share a regime and which are distinct.
"""
