"""Classe base condivisa dagli agenti che chiamano Claude con output strutturato."""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from common.claude_client import ClaudeClient

T = TypeVar("T", bound=BaseModel)


class Agent:
    def __init__(self, claude_client: ClaudeClient, system_prompt: str) -> None:
        self._claude_client = claude_client
        self._system_prompt = system_prompt

    def _run_structured(self, user_message: str, response_model: type[T], **kwargs: Any) -> T:
        return self._claude_client.run_structured(
            system_prompt=self._system_prompt,
            user_message=user_message,
            response_model=response_model,
            **kwargs,
        )
