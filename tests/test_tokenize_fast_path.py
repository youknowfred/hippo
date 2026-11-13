"""The tokenizer's ASCII fast path: a token that is pure ASCII skips the per-character CJK
check. The output must be exactly what the per-character check alone produced, on every
script mix, so the reference below IS the old loop."""

from __future__ import annotations

import pytest

from memory import build_index as B


def _reference(text: str):
    """``tokenize`` as it was before the fast path: every token's characters are checked."""
    if not text:
        return []
    out = []
    for m in B._TOKEN_RE.finditer(text.lower()):
        tok = m.group(0)
        if any(B._is_cjk_char(ch) for ch in tok):
            out.extend(B._split_cjk_and_word_runs(tok))
            continue
        if len(tok) < 2 or tok in B._STOPWORDS:
            continue
        out.append(tok)
    return out


@pytest.mark.parametrize(
    "text",
    [
        "",
        "deploy the canary rollout to production, then verify it",
        "snake_case CamelCase kebab-case v1.45.0 x86_64 a b c",
        "naïve café résumé façade İstanbul straße ǅ",
        "Привет мир, проверка токенов",
        "東京タワーの近くで会いましょう",
        "东京2024 deploy 北京abc def",
        "한국어 테스트 abc 123",
        "ｆｕｌｌwidth ＡＢＣ １２３",
        "emoji 🚀 and symbols ± × ÷ between words",
        "mixed_東京_snake and CJK中间ASCII",
    ],
)
def test_the_fast_path_changes_no_output(text):
    assert B.tokenize(text) == _reference(text)


def test_an_ascii_token_never_reaches_the_cjk_check(monkeypatch):
    calls = []
    real = B._is_cjk_char
    monkeypatch.setattr(B, "_is_cjk_char", lambda ch: calls.append(ch) or real(ch))
    assert B.tokenize("deploy the canary rollout") == ["deploy", "canary", "rollout"]
    assert calls == []
    B.tokenize("東京 deploy")
    assert calls and all(not ch.isascii() for ch in calls)
