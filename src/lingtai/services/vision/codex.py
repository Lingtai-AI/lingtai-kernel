"""Codex vision service — image analysis via ChatGPT Codex Responses API."""
from __future__ import annotations

import base64
from typing import Any

from ...auth.codex import CodexTokenManager
from . import VisionService, _read_image


class CodexVisionService(VisionService):
    """Image understanding via the ChatGPT Codex backend.

    Codex uses the ChatGPT OAuth token managed by the TUI, not an API key
    environment variable. The backend requires the Responses API with
    ``instructions``, ``stream=True``, and ``store=False``.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-5.5",
        base_url: str = "https://chatgpt.com/backend-api/codex",
        instructions: str = "You are a concise vision assistant.",
        # Codex's ChatGPT backend rejected max_output_tokens in a live test;
        # keep the default as None so the parameter is omitted unless a caller
        # explicitly opts in after revalidating backend support.
        max_output_tokens: int | None = None,
        timeout: float = 120.0,
        token_path: str | None = None,
        **_ignored: Any,
    ) -> None:
        import openai as _openai

        self._openai = _openai
        self._token_manager = CodexTokenManager(token_path=token_path)
        self._model = model
        self._base_url = base_url
        self._instructions = instructions
        self._max_output_tokens = max_output_tokens
        self._timeout = timeout

    def analyze_image(self, image_path: str, prompt: str | None = None) -> str:
        """Analyze an image using Codex's Responses API image input."""
        image_bytes, mime_type = _read_image(image_path)
        question = prompt or "Describe this image."

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64}"

        # Refresh both bearer and account metadata for every standalone image
        # request. Account ids are non-secret and only identify the ChatGPT
        # account; omit the header when the selected auth data has none.
        access_token = self._token_manager.get_access_token()
        account_id = self._token_manager.get_account_id()
        client_kwargs: dict[str, Any] = {
            "api_key": access_token,
            "base_url": self._base_url,
            "timeout": self._timeout,
        }
        if isinstance(account_id, str) and account_id:
            client_kwargs["default_headers"] = {"ChatGPT-Account-ID": account_id}
        client = self._openai.OpenAI(**client_kwargs)
        kwargs: dict[str, Any] = {
            "model": self._model,
            "instructions": self._instructions,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": question},
                        {"type": "input_image", "image_url": data_url},
                    ],
                }
            ],
            "stream": True,
            "store": False,
        }
        if self._max_output_tokens is not None:
            kwargs["max_output_tokens"] = self._max_output_tokens

        stream = client.responses.create(**kwargs)
        chunks: list[str] = []
        for event in stream:
            if getattr(event, "type", "") == "response.output_text.delta":
                chunks.append(getattr(event, "delta", "") or "")
        return "".join(chunks).strip()
