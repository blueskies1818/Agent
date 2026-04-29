# Providers & Agent Base

The provider layer decouples the rest of the codebase from any specific LLM API. The engine talks to a `BaseAgent` interface; the concrete implementation (Anthropic, OpenAI, or anything else) lives in a single file under `providers/`.

---

## `providers/base.py` — `BaseAgent`

Every provider must subclass `BaseAgent` and implement two private methods. Everything else — the public API the engine calls — lives in the base class so it stays consistent across providers.

```python
class BaseAgent(ABC):

    def call(self, messages: list[dict], system: str) -> str:
        """Single-shot completion. Returns the full reply as a string."""

    def stream(self, messages: list[dict], system: str) -> Iterator[str]:
        """Streaming completion. Yields text chunks as they arrive."""

    @abstractmethod
    def _raw_call(self, messages: list[dict], system: str) -> str:
        """Send messages to the API and return the complete reply text."""

    @abstractmethod
    def _raw_stream(self, messages: list[dict], system: str) -> Iterator[str]:
        """Send messages to the API and yield reply text in chunks."""
```

**Arguments:**

| Argument | Type | Description |
|----------|------|-------------|
| `messages` | `list[dict]` | `[{"role": "user"\|"assistant", "content": "..."}]` |
| `system` | `str` | System prompt string, built fresh each turn |

The engine calls `.call()` for single-shot completions and `.stream()` when `STREAM=True`. Providers only need to implement the two abstract `_raw_*` methods; the public wrappers handle nothing beyond the delegation.

---

## `providers/` — Dynamic loader

`providers/__init__.py` exposes one function:

```python
from providers import load_provider

agent = load_provider("claude")    # → ClaudeAgent instance
agent = load_provider("openai")    # → OpenAIAgent instance
```

`load_provider(name)` does three things:
1. Imports `providers.<name>` as a module.
2. Looks up the expected class name in the `_CLASS_NAMES` registry inside `__init__.py`.
3. Instantiates and returns the class.

If the module is missing or the class name is not in `_CLASS_NAMES`, it raises a `ValueError` with a clear message.

```python
_CLASS_NAMES: dict[str, str] = {
    "claude": "ClaudeAgent",
    "openai": "OpenAIAgent",
}
```

---

## `providers/claude.py` — Anthropic Claude

```python
class ClaudeAgent(BaseAgent):
    def __init__(self):
        self._client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def _raw_call(self, messages, system) -> str:
        response = self._client.messages.create(
            model=_MODEL,        # from config.PROVIDERS["claude"]["model"]
            max_tokens=4096,
            system=system,
            messages=messages,
        )
        return response.content[0].text

    def _raw_stream(self, messages, system) -> Iterator[str]:
        with self._client.messages.stream(
            model=_MODEL, max_tokens=4096, system=system, messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
```

`_MODEL` is read from `config.PROVIDERS["claude"]["model"]` at import time. Override it with the `CLAUDE_MODEL` env var.

**Requires:** `ANTHROPIC_API_KEY` in environment.

---

## `providers/openai.py` — OpenAI

```python
class OpenAIAgent(BaseAgent):
    def __init__(self):
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def _raw_call(self, messages, system) -> str:
        response = self._client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "system", "content": system}] + messages,
            max_completion_tokens=4096,
        )
        return response.choices[0].message.content
```

The key structural difference from Claude: OpenAI's API does not accept a top-level `system` parameter — the system prompt is prepended to `messages` as `{"role": "system", ...}`. Override the model with `OPENAI_MODEL`.

**Requires:** `OPENAI_API_KEY` in environment.

---

## Model selection at runtime

Provider is configured per **role** (planner, worker) via the `AGENTS` dict in `config.py`, populated from environment variables:

```python
AGENTS = {
    "planner": {
        "provider": os.getenv("PLANNER_PROVIDER", "openai"),
    },
    "worker": {
        "provider": os.getenv("WORKER_PROVIDER", "openai"),
    },
}
```

`engine/loop.py` calls `load_provider(AGENTS["planner"]["provider"])` and `load_provider(AGENTS["worker"]["provider"])` at session start. The model used is the `model` string from `config.PROVIDERS[provider]`. There is one model per provider — no fast/smart tiers.

To run the planner on Claude and the worker on OpenAI:

```bash
PLANNER_PROVIDER=claude WORKER_PROVIDER=openai ./start.sh
```

To override the model for a provider:

```bash
PLANNER_PROVIDER=claude CLAUDE_MODEL=claude-opus-4-7 ./start.sh
```

---

## Multimodal message format

When a mod returns `MediaAttachment` objects, `engine/media.py` serializes them into provider-specific content blocks before they reach the LLM. The format differs by provider:

**Anthropic (Claude):**

```python
{
    "type": "image",
    "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": "<b64-encoded-bytes>",
    }
}
```

**OpenAI:**

```python
{
    "type": "image_url",
    "image_url": {
        "url": "data:image/png;base64,<b64-encoded-bytes>",
        "detail": "low",
    }
}
```

The serializer reads the active provider's `media_format` key from `config.PROVIDERS` to decide which format to produce. Supported MIME types are listed in `media_caps`. If the provider does not support the attachment's MIME type, `process()` returns `None` and the attachment is dropped silently — the text portion of the message still reaches the LLM.

---

## Adding a new provider

1. Create `providers/<name>.py` and subclass `BaseAgent`:

```python
# providers/myprovider.py
from providers.base import BaseAgent
from config import PROVIDERS

_MODEL = PROVIDERS["myprovider"]["model"]

class MyProviderAgent(BaseAgent):

    def __init__(self):
        import my_sdk
        self._client = my_sdk.Client(api_key=os.getenv("MYPROVIDER_API_KEY"))

    def _raw_call(self, messages, system) -> str:
        ...

    def _raw_stream(self, messages, system) -> Iterator[str]:
        ...
```

2. Register the class name in `providers/__init__.py`:

```python
_CLASS_NAMES = {
    "claude":      "ClaudeAgent",
    "openai":      "OpenAIAgent",
    "myprovider":  "MyProviderAgent",   # add this line
}
```

3. Add the model string and capabilities to `config.py`:

```python
PROVIDERS = {
    ...
    "myprovider": {
        "model":        os.getenv("MYPROVIDER_MODEL", "myprovider-v1"),
        "media_format": "anthropic",           # or "openai", or add a new format to media.py
        "media_caps":   ["image/png"],
    },
}
```

4. Set the env var to activate it:

```bash
PLANNER_PROVIDER=myprovider ./start.sh
```

No other files need to change.


[[overview]]
