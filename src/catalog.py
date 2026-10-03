import re

ITEMS = (
    "external_ssd",
    "monitor",
    "laptop",
    "dock",
    "headset",
    "keyboard",
    "webcam",
    "mouse",
)
REASONS = ("new_hire", "preference", "performance", "broken", "lost")
FLAG_REASONS = {"not_found", "no_rule", "early_replacement", "ambiguous"}


def canonical_item(item: str) -> str:
    text = re.sub(r"[\s_]+", " ", item.lower().strip())
    candidates = [text]
    if text.endswith("s"):
        candidates.append(text[:-1])
    for candidate in candidates:
        for name in ITEMS:
            if candidate == name.replace("_", " "):
                return name
    return item.strip()


def canonical_reason(reason: str) -> str:
    lower = reason.lower()
    for name in REASONS:
        phrase = name.replace("_", " ")
        if re.search(rf"\b{re.escape(name)}\b", lower) or re.search(rf"\b{re.escape(phrase)}\b", lower):
            return name
    if re.search(r"\bbroke\b", lower):
        return "broken"
    return reason.strip()
