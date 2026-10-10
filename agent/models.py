"""List the models the configured Groq key can use:  python -m agent.models

Marks which are in a known open-weights family (hard rule 2). Prints model ids only; the key is never shown."""
from __future__ import annotations

from agent.settings import load_settings
from mcp_server.rules import load_rules


def main() -> None:
    settings = load_settings()
    if settings.provider != "groq":
        print(f"Provider is {settings.provider!r}: run `ollama list` for local models.")
        return
    from groq import Groq
    families = load_rules()["agent"]["open_weights_families"]
    print(f"Models available to this Groq key (configured MODEL = {settings.model}):")
    for m in sorted(Groq(api_key=settings.groq_api_key).models.list().data, key=lambda m: m.id):
        open_w = any(f in m.id.lower() for f in families)
        print(f"  {m.id:42} owner={m.owned_by:14} context={getattr(m, 'context_window', '?'):>7} "
              f"{'open-weights family' if open_w else '-'}")


if __name__ == "__main__":
    main()
