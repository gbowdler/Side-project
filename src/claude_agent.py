import anthropic
import os

_client = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _client


def ask_claude(prompt: str, system: str | None = None, max_tokens: int = 2048) -> str:
    client = get_client()
    kwargs = {
        "model": "claude-opus-4-8",
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive"},
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)
    for block in response.content:
        if block.type == "text":
            return block.text
    return ""
