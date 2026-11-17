from __future__ import annotations

import httpx
import json

from src.core.config import Settings
from src.core.llm.base import LLMClient, LLMResponse
from src.core.rag.exceptions import LLMError


class OllamaClient(LLMClient):
    """Ollama /chat endpoint. Удобно для локальной разработки без затрат на API."""

    def __init__(self, settings: Settings) -> None:
        self._base = settings.ollama_base_url.rstrip("/")
        self._model = settings.llm_model
        self._timeout = settings.llm_timeout_s
        self._num_ctx = settings.ollama_num_ctx
        self._think = settings.ollama_think
        self.model_name = f"ollama:{self._model}"

    def _thinking_options(self):
        if self._think is not None:
            return {"think": self._think}
        return {"think": False} if self._model.startswith("qwen3") and "instruct" in self._model else {}

    async def stream(self, messages, *, temperature=0.2, max_tokens=800):
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", f"{self._base}/api/chat", json={
                    "model": self._model, "messages": messages, "stream": True,
                    **self._thinking_options(),
                    "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": self._num_ctx},
                }) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise LLMError("Ollama stream failed")
                        yield {"delta": data.get("message", {}).get("content", ""),
                               "usage": {"model": self.model_name,
                                   "prompt_tokens": data.get("prompt_eval_count", 0),
                                   "completion_tokens": data.get("eval_count", 0)} if data.get("done") else {}}
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError("Ollama stream unavailable") from exc

    async def generate(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": self._num_ctx},
        }
        payload.update(self._thinking_options())
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(f"{self._base}/api/chat", json=payload)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            raise LLMError(f"Ollama request failed: {exc}") from exc

        final_text = data["message"]["content"].strip()
        if not final_text:
            raise LLMError("The model returned no final answer")
        return LLMResponse(
            text=final_text,
            usage={
                "model": self.model_name,
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
            },
        )

    async def generate_structured(self, messages, schema, *, max_tokens=200):
        """Constrain syntax only. The caller still validates source references."""
        payload = {'model': self._model, 'messages': messages, 'stream': False,
                   'format': schema, **self._thinking_options(),
                   'options': {'temperature': 0, 'num_predict': max_tokens, 'num_ctx': self._num_ctx}}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(f'{self._base}/api/chat', json=payload)
                response.raise_for_status(); data = response.json()
            if data.get('done_reason') == 'length':
                raise LLMError('Structured selection exceeded output limit')
            text = data['message']['content'].strip()
            if not text: raise LLMError('Empty structured selection')
            return LLMResponse(text=text, usage={'model': self.model_name,
                'prompt_tokens': data.get('prompt_eval_count', 0), 'completion_tokens': data.get('eval_count', 0)})
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise LLMError('Structured selection unavailable') from exc
