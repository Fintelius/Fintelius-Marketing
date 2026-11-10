"""Anthropic API wrapper. Web search is a built-in tool of the API (must be enabled in the Anthropic console)."""
import json
import re
import anthropic
from config import settings

_client = None


def client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def ask(system, user, max_tokens=3000, web=False, max_searches=5):
    kw = dict(model=settings.model, max_tokens=max_tokens, system=system,
              messages=[{"role": "user", "content": user}])
    if web:
        kw["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}]
    resp = client().messages.create(**kw)
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def extract_json(text):
    """Pulls the first JSON object/array out of a model reply. Returns None if none found."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    for open_c, close_c in (("[", "]"), ("{", "}")):
        i = t.find(open_c)
        while i != -1:
            depth = 0
            for j in range(i, len(t)):
                if t[j] == open_c:
                    depth += 1
                elif t[j] == close_c:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(t[i:j + 1])
                        except Exception:
                            break
            i = t.find(open_c, i + 1)
    return None


def ask_json(system, user, **kw):
    return extract_json(ask(system, user, **kw))
