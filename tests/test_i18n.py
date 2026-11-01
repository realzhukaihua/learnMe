"""Both languages are complete: every tr() string has Chinese, both prompt sets match, and the UI
dictionary covers every string the UI looks up."""

import ast
import re
import string
from pathlib import Path

from server.locales.zh import ZH
from server.prompts import en, zh

ROOT = Path(__file__).resolve().parent.parent


def tr_strings() -> set[str]:
    out = set()
    for path in (ROOT / "server").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    out.add(arg.value)
    return out


def fields(s: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


def test_every_server_string_has_chinese():
    missing = sorted(tr_strings() - set(ZH))
    assert not missing, missing


def test_chinese_keeps_the_placeholders():
    for en_text, zh_text in ZH.items():
        assert fields(en_text) == fields(zh_text), en_text


def test_prompt_sets_match():
    names = {n for n in dir(en) if n.isupper()}
    assert names == {n for n in dir(zh) if n.isupper()}
    for n in names:
        a, b = getattr(en, n), getattr(zh, n)
        if isinstance(a, str):
            assert fields(a) == fields(b), n
        else:
            assert set(a) == set(b), n


def test_ui_dictionary_covers_the_ui():
    js = (ROOT / "web" / "app.js").read_text() + (ROOT / "web" / "fighter.js").read_text()
    html = (ROOT / "web" / "index.html").read_text()
    used = {m.group(2).replace("\\'", "'") for m in re.finditer(r"""tr\((['"])((?:\\.|(?!\1).)*)\1""", js)}
    used |= {m.group(2).replace("&lt;", "<").replace("&gt;", ">")
             for m in re.finditer(r"""data-i18n(?:-html|-title|-placeholder)?=(['"])(.*?)\1""", html)}
    dic = (ROOT / "web" / "i18n.js").read_text()
    missing = sorted(k for k in used if f"'{k}'" not in dic and f'"{k}"' not in dic)
    assert not missing, missing
