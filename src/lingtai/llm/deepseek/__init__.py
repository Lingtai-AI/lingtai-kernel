"""DeepSeek provider-local behavior.

DeepSeek runs on the generic OpenAI-compatible transport (there is no
``DeepSeekAdapter`` subclass). What is genuinely DeepSeek-specific — its
reasoning-effort capability surface, omission/default rule, alias
normalization, and per-wire payload shape — lives here and is injected into
that transport by ``lingtai/llm/_register.py``.
"""
