"""Категория желаемой причёски: пожелание пользователя, не распознавание пола."""

import re

HAIR_STYLES = {"men": "Мужские", "women": "Женские", "unisex": "Универсальные"}
PATTERNS = {
    "men": r"\b(?:мужск[а-яё]*|мужчин[а-яё]*|masculine|men(?:'s)?)\b",
    "women": r"\b(?:женск[а-яё]*|женщин[а-яё]*|feminine|women(?:'s)?)\b",
    "unisex": r"\b(?:унисекс|универсальн[а-яё]*|unisex)\b",
}

HAIR_RULES = (
    "Hairstyle selection rules: Follow the user's explicitly requested masculine, feminine or "
    "unisex hairstyle category in EVERY panel of a collage; do not mix in a different category. "
    "A 'Категория причёсок' header is the final selected category and overrides earlier category "
    "mentions in the details. "
    "The category describes the requested HAIRSTYLE, not the person's gender. Never infer gender "
    "from a name or equate long hair with women and short hair with men. The user's explicit "
    "style and length requests take precedence over assumptions from appearance. If no category "
    "is specified, preserve the source person's existing gender presentation instead of changing it. "
    "Keep the same face, facial structure, age, eyebrows, facial hair, makeup, clothing and body "
    "in every panel unless a separate change is explicitly requested. Fit hair naturally to the "
    "head and hairline; vary only the requested hair features. "
)


def requested_hair_style(text):
    # Ambiguous and negated wishes are clarified by buttons, not guessed by an LLM.
    found = set()
    text = text.casefold()
    for key, pattern in PATTERNS.items():
        for match in re.finditer(pattern, text):
            clause = re.split(r"[,;.!?]|\b(?:а|но|but)\b", text[:match.start()])[-1]
            if not re.search(r"\b(?:не|not|без)\b", clause):
                found.add(key)
    return next(iter(found)) if len(found) == 1 else None


def mentions_hair_style(text):
    return any(re.search(pattern, text, re.I) for pattern in PATTERNS.values())


def hair_request(description, style):
    if style not in HAIR_STYLES:
        raise ValueError("invalid_hair_style")
    return f"Категория причёсок: {HAIR_STYLES[style].lower()}.\n{description}"
