DEFAULTS = {
    "api_compat": "openai",
    "base_url": None,
    "api_key_env": "OPENAI_API_KEY",
    "model": "",
    # Preserve the existing Responses preference for consumers that inject this
    # provider metadata. ``wire_api=auto`` delegates to that legacy flag, while
    # an explicit selector wins. Bare LLMService/OpenAIAdapter construction does
    # not load this mapping and therefore keeps its existing Chat default.
    "use_responses_api": True,
    "wire_api": "auto",
}
