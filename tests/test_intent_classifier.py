from types import SimpleNamespace

import nodes


class _FakeLLM:
    def __init__(self, fallback_text: str):
        self._fallback_text = fallback_text

    def with_structured_output(self, _schema):
        raise RuntimeError("structured output unsupported")

    def invoke(self, _messages):
        return SimpleNamespace(content=self._fallback_text)


def test_intent_fallback_uses_explicit_token(monkeypatch):
    monkeypatch.setattr(
        nodes,
        "get_llm",
        lambda model=None, temperature=None, model_kwargs=None: _FakeLLM("question"),
    )
    result = nodes.intent_classifier({"user_message": "create inverter"})
    assert result["intent"] == "question"


def test_intent_fallback_uses_user_message_heuristic_for_generation(monkeypatch):
    monkeypatch.setattr(
        nodes,
        "get_llm",
        lambda model=None, temperature=None, model_kwargs=None: _FakeLLM("unclear response"),
    )
    result = nodes.intent_classifier({"user_message": "please create a nand gate"})
    assert result["intent"] == "generate"


def test_intent_fallback_uses_user_message_heuristic_for_question(monkeypatch):
    monkeypatch.setattr(
        nodes,
        "get_llm",
        lambda model=None, temperature=None, model_kwargs=None: _FakeLLM("not sure"),
    )
    result = nodes.intent_classifier({"user_message": "What does a path declaration mean?"})
    assert result["intent"] == "question"
