"""Wrapper sottile attorno all'SDK Anthropic per ottenere output strutturato e validato.

Ogni agente chiama `ClaudeClient.run_structured(...)` passando il proprio modello
Pydantic di output: Claude viene forzato a rispondere tramite un tool call
"emit_result" il cui schema corrisponde al modello, così l'output è sempre
JSON valido o la chiamata fallisce esplicitamente (nessun parsing fragile di
testo libero). Se l'agente ha bisogno di recuperare dati esterni (news, prezzi),
può fornire tool aggiuntivi con un `tool_executor` che li esegue.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_EMIT_RESULT_TOOL_NAME = "emit_result"


class StructuredOutputError(RuntimeError):
    """Sollevata quando Claude non produce un output conforme allo schema atteso."""


class ClaudeClient:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)),
    )
    def _create_message(self, **kwargs: Any) -> Any:
        return self._client.messages.create(**kwargs)

    def run_structured(
        self,
        system_prompt: str,
        user_message: str,
        response_model: type[T],
        extra_tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[str, dict[str, Any]], str] | None = None,
        max_tool_iterations: int = 6,
        max_tokens: int = 4096,
    ) -> T:
        emit_tool = {
            "name": _EMIT_RESULT_TOOL_NAME,
            "description": f"Restituisce il risultato finale conforme allo schema {response_model.__name__}.",
            "input_schema": response_model.model_json_schema(),
        }
        tools = [*(extra_tools or []), emit_tool]

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

        for iteration in range(max_tool_iterations):
            is_last_chance = iteration == max_tool_iterations - 1
            response = self._create_message(
                model=self._model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=messages,
                tools=tools,
                tool_choice={"type": "tool", "name": _EMIT_RESULT_TOOL_NAME} if is_last_chance else {"type": "auto"},
            )

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": f"Devi rispondere esclusivamente tramite il tool '{_EMIT_RESULT_TOOL_NAME}'.",
                    }
                )
                continue

            messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []
            final_result: T | None = None

            for tool_use in tool_uses:
                if tool_use.name == _EMIT_RESULT_TOOL_NAME:
                    try:
                        final_result = response_model.model_validate(tool_use.input)
                    except ValidationError as exc:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use.id,
                                "content": f"Output non valido, correggi: {exc}",
                                "is_error": True,
                            }
                        )
                        continue
                elif tool_executor is not None:
                    try:
                        result_text = tool_executor(tool_use.name, tool_use.input)
                    except Exception as exc:  # noqa: BLE001 - vogliamo comunque restituire l'errore a Claude
                        result_text = f"Errore nell'esecuzione del tool: {exc}"
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": tool_use.id, "content": result_text}
                    )
                else:
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "content": f"Tool '{tool_use.name}' sconosciuto.",
                            "is_error": True,
                        }
                    )

            if final_result is not None:
                return final_result

            messages.append({"role": "user", "content": tool_results})

        raise StructuredOutputError(
            f"Claude non ha prodotto un output valido per {response_model.__name__} entro {max_tool_iterations} iterazioni."
        )


def json_dumps_compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)
