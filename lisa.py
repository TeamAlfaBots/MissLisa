"""
Lisa dataset binding: finds the few most relevant example chats for the incoming message and turns them
into a short "tone examples" block for the system prompt (few-shot, no fine-tuning needed).

Data: data/lisa_examples.jsonl.gz  (lines: {"c": category, "s": subcategory, "m": [user, assistant, ...]})
All examples are synthetic (see data/lisa_persona.json). Retrieval is pure Python (no extra libraries):
an inverted index over the user lines, IDF-weighted word overlap, then a random pick among the best few
so replies do not become copies of one example.
"""
import gzip
import json
import logging
import math
import os
import random
import re
from collections import Counter, defaultdict

from config import LISA_DATASET, LISA_EXAMPLE_CHARS, LISA_EXAMPLES

log = logging.getLogger("girl-chatbot.lisa")

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lisa_examples.jsonl.gz")

_ALIAS = {"nhi": "nahi", "nahin": "nahi", "kr": "kar", "kro": "karo", "btao": "batao", "rha": "raha", "rhi": "rahi",
          "bnao": "banao", "hu": "hoon", "hun": "hoon", "bht": "bahut", "pls": "please", "plz": "please", "or": "aur"}
_STOP = {"hai", "kya", "ho", "me", "ko", "ka", "ki", "ke", "se", "to", "toh", "ye", "yeh", "yaar", "mujhe", "mera", "main",
         "ek", "na", "bhi", "hi", "aur", "hoon", "tum", "tu", "lisa", "the", "is", "a", "i", "you"}
_WORD = re.compile(r"[a-z0-9]+")
_CAP = re.compile(r"\b[A-Z][a-z]+\b")

LISA_RULES = (
    "\n\nLISA STYLE (extra rules, upar ke texting rules ke saath):\n"
    "- Pehle ek chhota reaction do, phir user ki baat pe comment, aur zyada se zyada ek sawal.\n"
    "- Isi chat me user ne pehle jo bataya (exam, trip, kaam, mood) use yaad rakho aur kabhi kabhi refer karo.\n"
    "- Shuruat ke shabd aur sentence ki length badalte raho; kabhi sirf 2-3 words ka reply bhi theek hai.\n"
    "- Halki-phulki raay de sakti ho (chai ya coffee, movie), par apni jhooti life-story ya experience mat banao.\n"
    "- User ka mood dekho: mazaak me playful, udaas me shaant aur soft.\n"
    "- Koi romantic partner, girlfriend ya hamesha-saath-rehne wale promise wali role mat lo; pyaar se mana karo "
    "aur apne asli dost/family ki taraf bhejo."
)


def tokens(text: str) -> list:
    words = _WORD.findall((text or "").lower())
    return [_ALIAS.get(w, w) for w in words if _ALIAS.get(w, w) not in _STOP and len(w) > 1]


def chatify(text: str, max_chars: int) -> str:
    """Make a dataset line look like the texting style in the prompt: lowercase, no final full stop."""
    text = _CAP.sub(lambda m: m.group(0).lower(), text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith("."):
        text = text[:-1]
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


class LisaExamples:
    def __init__(self, path: str = DATA_FILE, rng: "random.Random | None" = None):
        self.path = path
        self.rng = rng or random.Random()
        self.items: list = []          # (category, subcategory, user, assistant)
        self.index: dict = defaultdict(list)
        self.loaded = False

    def load(self) -> int:
        self.items.clear()
        self.index.clear()
        try:
            opener = gzip.open if self.path.endswith(".gz") else open
            with opener(self.path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                        msgs = row["m"]
                        user, reply = msgs[0], msgs[1]
                    except (ValueError, KeyError, IndexError):
                        continue
                    idx = len(self.items)
                    self.items.append((row.get("c", ""), row.get("s", ""), user, reply))
                    for t in set(tokens(user)):
                        self.index[t].append(idx)
        except FileNotFoundError:
            log.warning("Lisa dataset not found at %s - continuing without examples", self.path)
            return 0
        except Exception as exc:
            log.warning("Lisa dataset could not be loaded (%s)", type(exc).__name__)
            return 0
        self.loaded = bool(self.items)
        log.info("Lisa dataset loaded: %s example chats", len(self.items))
        return len(self.items)

    def search(self, text: str, k: int = 3) -> list:
        """Top-k (user, reply) pairs; at most one per subcategory; random among the best ~3k candidates."""
        if not self.loaded or k <= 0:
            return []
        q = set(tokens(text))
        n = len(self.items)
        scores: Counter = Counter()
        for t in q:
            ids = self.index.get(t)
            if not ids:
                continue
            idf = math.log(1 + n / len(ids))
            for i in ids:
                scores[i] += idf
        if not scores:
            return []
        pool = [i for i, _ in scores.most_common(k * 6)]
        top = scores[pool[0]]
        pool = [i for i in pool if scores[i] >= top * 0.4][: k * 3]
        self.rng.shuffle(pool)
        picked, seen = [], set()
        for i in pool:
            cat, sub, user, reply = self.items[i]
            if sub in seen or user.strip().lower() == (text or "").strip().lower():
                continue
            seen.add(sub)
            picked.append((user, reply))
            if len(picked) == k:
                break
        return picked

    def block(self, text: str, k: int = LISA_EXAMPLES, max_chars: int = LISA_EXAMPLE_CHARS) -> str:
        pairs = self.search(text, k)
        if not pairs:
            return ""
        lines = []
        for user, reply in pairs:
            lines.append(f"user: {chatify(user, max_chars)}")
            lines.append(f"tum: {chatify(reply, max_chars)}")
        return (
            "\n\nTONE EXAMPLES (milte-julte chats; sirf vibe aur style samajhne ke liye, shabd copy mat karo, "
            "user ko ye kabhi mat batana):\n" + "\n".join(lines)
        )


_instance: "LisaExamples | None" = None


def get() -> "LisaExamples | None":
    """Lazy singleton; None when disabled (LISA_DATASET=0) or the file is missing."""
    global _instance
    if not LISA_DATASET:
        return None
    if _instance is None:
        _instance = LisaExamples()
        _instance.load()
    return _instance if _instance.loaded else None


def prompt_extra(text: str) -> str:
    """Rules + tone examples for one reply ('' when the dataset is off)."""
    inst = get()
    if inst is None:
        return ""
    return LISA_RULES + inst.block(text)


def info() -> dict:
    inst = get()
    return {"enabled": bool(LISA_DATASET), "loaded": bool(inst), "examples": len(inst.items) if inst else 0, "per_reply": LISA_EXAMPLES}
