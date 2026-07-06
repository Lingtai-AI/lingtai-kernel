"""Unit tests for load_jsonc — string-aware comment and trailing-comma handling."""

from lingtai_kernel.config_resolve import load_jsonc


def _load(tmp_path, body: str) -> dict:
    p = tmp_path / "cfg.jsonc"
    p.write_text(body, encoding="utf-8")
    return load_jsonc(p)


def test_load_jsonc_preserves_comma_bracket_inside_strings(tmp_path):
    """Regression for #725: ", ]" / ", }" inside string values must survive."""
    data = _load(tmp_path, '{"pattern": "a, ]b", "cmd": "x, }y"}')
    assert data == {"pattern": "a, ]b", "cmd": "x, }y"}


def test_load_jsonc_preserves_comma_whitespace_bracket_inside_strings(tmp_path):
    """The \\s* span (including newlines) inside a string is untouched."""
    body = '{"snippet": "end,\\n  ]"}'
    data = _load(tmp_path, body)
    assert data == {"snippet": "end,\n  ]"}


def test_load_jsonc_still_strips_trailing_commas(tmp_path):
    data = _load(tmp_path, '{"a": [1, 2, ], "b": {"c": 1, }, }')
    assert data == {"a": [1, 2], "b": {"c": 1}}


def test_load_jsonc_trailing_comma_before_comment(tmp_path):
    """Comments strip before the comma pass, so ", // note\\n}" collapses."""
    data = _load(tmp_path, '{"a": 1,  // note\n}')
    assert data == {"a": 1}


def test_load_jsonc_comment_and_comma_features_compose_with_strings(tmp_path):
    body = '''{
      "url": "https://host/x",  // comment after a URL value
      "pattern": "match, ]end",
      "list": [1, 2, ],
    }'''
    data = _load(tmp_path, body)
    assert data == {
        "url": "https://host/x",
        "pattern": "match, ]end",
        "list": [1, 2],
    }


def test_load_jsonc_escaped_quote_before_comma_bracket(tmp_path):
    """Escaped quotes inside a string don't break the string tokenizer."""
    data = _load(tmp_path, '{"v": "end \\", ]"}')
    assert data == {"v": 'end ", ]'}
