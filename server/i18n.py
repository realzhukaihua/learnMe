"""Two languages, English by default.

Short strings are written in English in the code and wrapped in tr(); server/locales/zh.py maps
each English source string to its Chinese text (gettext style, so a missing entry falls back to
English instead of breaking). Long prompts live in server/prompts/{en,zh}.py.
"""

from .config import CONFIG
from .locales.zh import ZH

LANG = CONFIG.language


def tr(text: str, **values) -> str:
    out = ZH.get(text, text) if LANG == "zh" else text
    return out.format(**values) if values else out
