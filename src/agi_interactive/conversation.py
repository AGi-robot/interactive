"""Run one text conversation turn and emit the WebUI-compatible event stream."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from time import perf_counter
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import httpx


class ConversationMemory(Protocol):
    """Minimal Memory interface required by the conversation engine."""

    def recent(self) -> list[dict[str, Any]]: ...

    def add_exchange(self, user_text: str, assistant_text: str) -> None: ...


class EventLogger(Protocol):
    """Logger methods used by the engine and supplied by its host runtime."""

    def info(self, message: str) -> None: ...

    def error(self, message: str) -> None: ...


class ConversationEngine:
    """Handle text turns without owning transport, process, or storage setup."""

    def __init__(
        self,
        *,
        memory: ConversationMemory,
        publish: Callable[[dict[str, Any]], None],
        base_url: str,
        model: str,
        system_prompt: str,
        api_key: str = "",
        timezone_name: str = "America/Vancouver",
        timeout: float = 120,
        stream: Callable[..., Any] | None = None,
        logger: EventLogger | None = None,
    ) -> None:
        self.memory = memory
        self.publish = publish
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.system_prompt = system_prompt
        self.api_key = api_key
        self.timezone_name = timezone_name
        self.timeout = timeout
        self.stream = stream or httpx.stream
        self.logger = logger or logging.getLogger("agi_interactive")

    def answer(self, text: str, identifier: str = "") -> None:
        """Process one user turn and publish streamed response events."""
        started_at = perf_counter()
        try:
            now = datetime.now(ZoneInfo(self.timezone_name))
            normalized_text = text.casefold()
            asks_for_clock = bool(re.search(
                r"\b(?:what(?:'s| is)?\s+(?:the\s+)?time(?:\s+is\s+it)?|"
                r"what\s+time\s+is\s+it|do\s+you\s+know\s+(?:(?:what|the)\s+)?time(?:\s+it\s+is)?|"
                r"tell\s+me\s+(?:the\s+)?time|current\s+time|time\s+(?:right\s+now|now|again))\b",
                normalized_text,
            ))
            asks_for_date = bool(re.search(
                r"\b(?:what(?:'s| is)?\s+(?:today's\s+)?date|what(?:'s| is)?\s+the\s+date\s+today|"
                r"what(?:'s| is)?\s+(?:the\s+)?day\s+(?:today|is\s+it)|current\s+date|today's\s+date|date\s+today)\b",
                normalized_text,
            ))
            if asks_for_clock or asks_for_date:
                self._answer_time(text, identifier, now, asks_for_clock, asks_for_date, started_at)
                return

            current_time = (
                f"Current local date and time: {now.strftime('%A, %B %d, %Y at %I:%M:%S %p %Z')} "
                f"in the {self.timezone_name} timezone (UTC offset {now.strftime('%z')}). "
                "Use this as the authoritative current time for the user."
            )
            messages = [{
                "role": "system",
                "content": f"{self.system_prompt}\n\n{current_time}",
            }]
            messages.extend(self.memory.recent())
            messages.append({"role": "user", "content": text})
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            self.publish({"type": "start", "content": "", "radio_id": identifier})
            answer_parts: list[str] = []
            stream_finished = False
            finish_reason = None
            usage: dict[str, Any] = {}
            timings: dict[str, Any] = {}
            with self.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
                headers=headers,
                timeout=self.timeout,
            ) as response:
                self.logger.info(f"LLM streaming request returned HTTP {response.status_code}")
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        stream_finished = True
                        break
                    try:
                        event = json.loads(payload)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(event.get("usage"), dict):
                        usage = event["usage"]
                    if isinstance(event.get("timings"), dict):
                        timings = event["timings"]
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    if choices[0].get("finish_reason") is not None:
                        finish_reason = choices[0]["finish_reason"]
                        stream_finished = True
                    chunk = choices[0].get("delta", {}).get("content", "")
                    if isinstance(chunk, str) and chunk:
                        answer_parts.append(chunk)
                        self.publish({"type": "delta", "content": chunk, "radio_id": identifier})

            if not stream_finished:
                raise ValueError("LLM stream ended before a completion marker")
            if finish_reason not in (None, "stop"):
                raise ValueError(f"LLM response was incomplete (finish reason: {finish_reason})")
            answer = "".join(answer_parts).strip()
            if not answer:
                raise ValueError("LLM returned an empty response")
            self.memory.add_exchange(text, answer)
            prompt_tokens = usage.get("prompt_tokens", timings.get("prompt_n"))
            completion_tokens = usage.get("completion_tokens", timings.get("predicted_n"))
            if prompt_tokens is not None and completion_tokens is not None:
                total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
            else:
                total_tokens = usage.get("total_tokens")
            self.publish({
                "type": "done",
                "content": "",
                "radio_id": identifier,
                "metrics": {
                    "latency_ms": round((perf_counter() - started_at) * 1000),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
            })
            self.logger.info(f"Published Grace reply (id={identifier})")
        except Exception as error:
            self.logger.error(f"AIVA LLM request failed: {error}")
            self.publish({
                "type": "error",
                "content": f"AIVA could not reach its LLM: {error}",
                "radio_id": identifier,
            })

    def _answer_time(
        self,
        text: str,
        identifier: str,
        now: datetime,
        asks_for_clock: bool,
        asks_for_date: bool,
        started_at: float,
    ) -> None:
        local_date = f"{now.strftime('%A, %B')} {now.day}, {now.year}"
        local_time = now.strftime("%I:%M %p").lstrip("0")
        if asks_for_clock and asks_for_date:
            answer = f"It’s {local_date} at {local_time} {now.tzname()} ({self.timezone_name})."
        elif asks_for_clock:
            answer = f"It’s {local_time} {now.tzname()} ({self.timezone_name}) on {local_date}."
        else:
            answer = f"Today is {local_date} ({self.timezone_name})."
        self.publish({"type": "start", "content": "", "radio_id": identifier})
        self.publish({"type": "delta", "content": answer, "radio_id": identifier})
        self.memory.add_exchange(text, answer)
        self.publish({
            "type": "done",
            "content": "",
            "radio_id": identifier,
            "metrics": {
                "latency_ms": round((perf_counter() - started_at) * 1000),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        })
