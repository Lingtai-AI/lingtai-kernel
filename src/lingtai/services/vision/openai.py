"""OpenAI vision service — standalone image analysis via OpenAI's multimodal API."""
from __future__ import annotations

from . import VisionService, _image_url_messages, _read_image, _require_api_key


class OpenAIVisionService(VisionService):
    """Image understanding via OpenAI's chat completions with vision.

    Owns its own ``openai.OpenAI`` client and API key — fully
    independent of any LLM adapter or agent.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str | None = None,
        max_tokens: int = 1024,
        default_headers: dict | None = None,
        wire_api: str = "chat_completions",
    ) -> None:
        api_key = _require_api_key(api_key, "openai")
        normalized_wire = wire_api.strip().lower() if isinstance(wire_api, str) else wire_api
        if not isinstance(normalized_wire, str) or normalized_wire not in {
            "chat_completions",
            "responses",
        }:
            raise ValueError(
                "Unsupported OpenAI vision wire; use vision(action='manual')."
            )

        import openai as _openai

        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        if default_headers:
            kwargs["default_headers"] = dict(default_headers)
        self._client = _openai.OpenAI(**kwargs)
        self._model = model
        self._max_tokens = max_tokens
        self._wire_api = normalized_wire

    def analyze_image(self, image_path: str, prompt: str | None = None) -> str:
        """Analyze an image using OpenAI's vision capabilities."""
        if self._wire_api == "responses":
            image_bytes, mime_type = _read_image(image_path)
            import base64
            data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('utf-8')}"
            raw = self._client.responses.create(
                model=self._model,
                max_output_tokens=self._max_tokens,
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": prompt or "Describe this image."},
                    {"type": "input_image", "image_url": data_url},
                ]}],
            )
            return getattr(raw, "output_text", "") or ""
        raw = self._client.chat.completions.create(
            model=self._model,
            messages=_image_url_messages(image_path, prompt),
            max_tokens=self._max_tokens,
        )
        if not hasattr(raw, "choices"):
            # The openai SDK returns the raw body (often a str) instead of a
            # ChatCompletion when the upstream serves non-JSON — an HTML SPA
            # route, a plain-text gateway error, etc. Accessing raw.choices
            # then raised the mystifying "'str' object has no attribute
            # 'choices'". Surface the actual body so the cause is visible.
            snippet = repr(raw)[:200] if isinstance(raw, str) else f"<{type(raw).__name__}>"
            raise RuntimeError(
                "vision upstream did not return a JSON ChatCompletion. "
                f"Got {type(raw).__name__}: {snippet}. "
                "Common cause: base_url missing the '/v1' suffix on a local "
                "proxy, or the proxy returning an HTML dashboard for unknown "
                "routes."
            )
        if raw.choices:
            return raw.choices[0].message.content or ""
        return ""
