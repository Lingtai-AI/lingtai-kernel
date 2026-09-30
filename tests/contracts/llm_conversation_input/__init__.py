"""Characterization suite for the LLM conversation *input* surface.

This package pins the behavior of the two inputs the kernel ``ChatSession`` ABC
declares — ``send(str)`` and ``send(list[ToolResultBlock])`` — against every
*selectable production return regime*, using a mocked transport (no network).

Three things are made executable (see ``regimes.py``):

* **Registry matrix** — every registered provider name (exactly ``openai``,
  ``anthropic``, ``codex``, ``claude-code``) is built through the real
  ``LLMService`` end to end (``LLMService(provider=<exact name>, ...)`` ->
  registered factory -> ``create_session``), with the SDK client mocked, and the
  returned adapter/session/``_GatedSession`` **class** is asserted. The mapping is
  therefore the data under test; it cannot drift away from what the factories do,
  and rebinding a provider to a different factory fails the matrix. The union of
  built provider names equals the registry key set. Every ``openai`` Responses
  row also asserts the built session's ``_stateless_replay`` bit (always
  ``True``: stateless full replay on the official endpoint and a compatible
  ``base_url`` alike), and that wire (full replay, no ``previous_response_id``)
  is proven through the same real ``LLMService`` route.

* **Wire-selector schema cross-product** — for every family across
  ``wire_api`` values, plus every removed provider name, schema *selectability*
  (``init_schema.validate_init``) is checked *separately* from the concrete
  adapter/session class the accepted configuration builds through the real
  ``LLMService`` path. Non-``auto`` ``wire_api`` is schema-valid only for
  ``openai``; removed provider names are rejected with a pointer to the
  ``openai``/``anthropic`` replacements.

* **Behavior regimes** — the concrete ``ChatSession`` configurations with
  distinct common-input wire behavior (including the reasoning-fallback-configured
  ``OpenAIChatSession``, the stateless Responses session, Codex's own REST
  machinery, and a ``_GatedSession``-wrapped session) are each driven through
  both inputs; the tests assert the exact provider wire AND the returned
  ``LLMResponse`` + concrete ``UsageMetadata``.

It is deliberately NOT a governed component: it adds no ``CONTRACT.md``, links
nothing from the root contract, and claims no Ports & Adapters migration. It is a
prerequisite characterization/correction layer whose job is to make the real
per-regime input behavior explicit and executable, so a *future* child contract
can know which concrete providers share a regime and which are distinct.
"""
