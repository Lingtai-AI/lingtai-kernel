"""Token counting with provider-agnostic fallback chain.

Priority: google.genai LocalTokenizer → tiktoken → len(text) // 4
"""
from __future__ import annotations

import warnings
from .logging import get_logger

_tokenizer = None
_backend: str = "none"


def _find_spec_safe(name: str):
    """``importlib.util.find_spec`` that never raises.

    find_spec imports parent packages and propagates any error they raise, so
    treat every failure as "not importable".
    """
    import importlib.util
    try:
        return importlib.util.find_spec(name)
    except Exception:
        return None


def _init_tokenizer() -> None:
    global _tokenizer, _backend
    logger = get_logger()

    # Try google-genai first.
    #
    # ``google.genai.local_tokenizer`` hard-imports ``sentencepiece`` at module
    # scope, and sentencepiece is not a declared dependency — so on a stock
    # install this branch always raises ModuleNotFoundError and we fall through.
    # But by then the ``google.genai`` package import has already run, resident
    # for the life of the process at a measured ~66MB RSS (it drags in
    # pydantic_core, cryptography, websockets, brotli, charset_normalizer).
    # Every process that counts a single token paid that — including daemon
    # execution children running an entirely different provider.
    #
    # Probe for sentencepiece with find_spec (no import, no cost) first. When it
    # is absent the google branch cannot succeed, so skipping it is exactly
    # equivalent to running it, minus the memory.
    if _find_spec_safe("sentencepiece") is None:
        logger.debug(
            "token_counter: sentencepiece absent, skipping google-genai "
            "LocalTokenizer (its import cannot succeed)"
        )
    else:
        try:
            from google.genai._common import ExperimentalWarning
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=ExperimentalWarning)
                from google.genai.local_tokenizer import LocalTokenizer
            _tokenizer = LocalTokenizer()
            _backend = "gemini"
            logger.debug("token_counter: using google-genai LocalTokenizer")
            return
        except (ImportError, Exception):
            pass

    # Try tiktoken
    try:
        import tiktoken
        _tokenizer = tiktoken.get_encoding("cl100k_base")
        _backend = "tiktoken"
        logger.debug("token_counter: using tiktoken cl100k_base")
        return
    except (ImportError, Exception):
        pass

    # Final fallback: character estimate
    _backend = "char_estimate"
    logger.debug("token_counter: using character estimate (len // 4)")


def count_tokens(text: str) -> int:
    """Count tokens in text using the best available tokenizer."""
    if not text:
        return 0
    global _tokenizer, _backend
    if _backend == "none":
        _init_tokenizer()

    if _backend == "gemini":
        return _tokenizer.count_tokens(text).total_tokens
    elif _backend == "tiktoken":
        return len(_tokenizer.encode(text))
    else:
        return len(text) // 4


def count_tool_tokens(schemas: list) -> int:
    """Estimate tokens consumed by tool schemas (dicts or FunctionSchema objects)."""
    import json
    dicts = [s.to_dict() if hasattr(s, "to_dict") else s for s in schemas]
    text = json.dumps(dicts)
    return count_tokens(text)
