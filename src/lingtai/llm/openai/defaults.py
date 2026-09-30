"""Provider metadata for the ``openai`` family (any OpenAI-compatible endpoint).

``base_url`` ``None`` means the official endpoint
(``lingtai.llm.openai.adapter.OPENAI_OFFICIAL_BASE_URL``); ``wire_api`` is
``chat_completions`` unless a manifest selects ``responses`` (stateless
full-history replay).
"""

DEFAULTS = {
    "base_url": None,
    "api_key_env": "OPENAI_API_KEY",
    "model": "",
    "wire_api": "chat_completions",
}
