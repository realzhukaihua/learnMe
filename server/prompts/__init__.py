"""Long prompts, in the configured language: `from .prompts import P; P.MANAGER`."""

from ..config import CONFIG
from . import en, zh

P = zh if CONFIG.language == "zh" else en
