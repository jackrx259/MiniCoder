import httpx
from openai import (
    OpenAI,
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    AuthenticationError,
    RateLimitError,
    InternalServerError,
)


class LLMClient:
    def __init__(self, api_key: str, api_base: str, model: str,
                 timeout: int = 60, max_retries: int = 3):
        self.model = model

        # Cumulative token usage tracking
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_requests = 0

        # The openai SDK handles retries, backoff, and connection pooling internally.
        # It is compatible with any OpenAI-format API via base_url.
        self._client = OpenAI(
            api_key=api_key,
            base_url=api_base,
            # Accepts float or httpx.Timeout; using the latter for per-phase control.
            timeout=httpx.Timeout(timeout, read=timeout, write=30.0, connect=10.0),
            max_retries=max_retries,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat_completion(self, messages: list, tools: list = None) -> dict:
        """Send a chat completion request via the openai SDK.

        The SDK transparently handles retries (429 / 5xx) with exponential
        back-off, so no manual retry loop is needed here.
        Returns a plain dict mirroring the OpenAI response schema so that
        the rest of the codebase needs no changes.
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = self._client.chat.completions.create(**kwargs)
        except AuthenticationError as e:
            # HTTP 401 — invalid or missing API key
            raise Exception(
                "❌ Authentication failed (HTTP 401). "
                "Please check your API key in config.json."
            ) from e
        except RateLimitError as e:
            # HTTP 429 — the SDK retries automatically; this fires only after all retries are exhausted
            raise Exception(f"⏳ Rate limit exceeded (HTTP 429): {e.message}") from e
        except InternalServerError as e:
            # HTTP >=500 — the SDK retries automatically; this fires only after all retries are exhausted
            raise Exception(f"🔌 Server error (HTTP {e.status_code}): {e.message}") from e
        except APIStatusError as e:
            # Other 4xx errors (400 BadRequest, 403 PermissionDenied, 404 NotFound, etc.)
            raise Exception(f"API error (HTTP {e.status_code}): {e.message}") from e
        except APITimeoutError as e:
            raise Exception(f"⏱ Request timed out: {e}") from e
        except APIConnectionError as e:
            raise Exception(f"🔌 Connection error: {e}") from e

        # Track cumulative token usage
        usage = response.usage
        if usage:
            self._total_prompt_tokens += usage.prompt_tokens or 0
            self._total_completion_tokens += usage.completion_tokens or 0
        self._total_requests += 1

        # Convert the SDK Pydantic response model to a plain dict.
        # to_dict() is the SDK-idiomatic method; it lets the rest of the
        # codebase use standard dict access without depending on Pydantic.
        return response.to_dict()

    # ------------------------------------------------------------------
    # Token usage
    # ------------------------------------------------------------------

    def get_usage_summary(self) -> str:
        """Return a human-readable token usage summary."""
        total = self._total_prompt_tokens + self._total_completion_tokens
        return (
            f"📊 Token Usage — "
            f"Prompt: {self._total_prompt_tokens:,} | "
            f"Completion: {self._total_completion_tokens:,} | "
            f"Total: {total:,} | "
            f"Requests: {self._total_requests}"
        )

    def get_last_turn_tokens(self, response_data: dict) -> str:
        """Return per-call token info from a response dict."""
        usage = response_data.get("usage") or {}
        p = usage.get("prompt_tokens", 0)
        c = usage.get("completion_tokens", 0)
        return f"[tokens: +{p}p /+{c}c]" if (p or c) else ""
