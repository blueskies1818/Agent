import importlib
from providers.base import BaseAgent

_CLASS_NAMES: dict[str, str] = {
    "claude": "ClaudeAgent",
    "openai": "OpenAIAgent",
}

def load_provider(name: str) -> BaseAgent:
    try:
        module = importlib.import_module(f"providers.{name}")
    except ModuleNotFoundError:
        raise ValueError(f"No provider module found at providers/{name}.py")

    class_name = _CLASS_NAMES.get(name)
    if class_name is None:
        raise ValueError(f"Provider '{name}' is not registered in _CLASS_NAMES inside providers/__init__.py")

    cls = getattr(module, class_name, None)
    if cls is None:
        raise ValueError(f"providers/{name}.py exists but does not define '{class_name}'")

    return cls()
