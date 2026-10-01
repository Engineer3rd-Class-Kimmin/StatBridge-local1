from __future__ import annotations

import re
from typing import Iterable


def norm_space(text: str) -> str:
    return ' '.join(str(text).split()).strip()


def normalize_text(text: str) -> str:
    text = norm_space(text).lower()
    text = re.sub(r'[^0-9a-zA-Z가-힣\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def tokenize(text: str) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    return [t for t in text.split(' ') if t]


def unique_join(parts: Iterable[str], sep: str = ' | ') -> str:
    seen = set()
    out: list[str] = []
    for p in parts:
        p = norm_space(p)
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return sep.join(out)
