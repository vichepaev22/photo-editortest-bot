from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    label: str
    instruction: str
    inputs: int = 1
    credits: int = 1


PRESETS = {
    "hair": Preset("Причёска", "Change only the hairstyle and hair color as requested."),
    "clothes": Preset("Одежда", "Change only the clothing as requested; keep the person fully clothed."),
    "glasses": Preset("Очки", "Add or change eyeglasses as requested, with realistic fit and reflections."),
    "background": Preset("Фон", "Change only the background and harmonize lighting as requested."),
    "enhance": Preset(
        "Улучшение фото", "Reduce noise and improve natural lighting without inventing identity details."
    ),
    "merge": Preset("Объединить два фото", "Combine both reference images in one natural composition.", 2, 2),
}

# Proposed prices, not a verified profitable tariff. Amounts are integer RUB kopecks.
PACKS = {"small": (5, 14900), "medium": (10, 24900), "large": (25, 54900)}


def prompt_for(preset: str, description: str) -> str:
    if preset not in PRESETS or not 1 <= len(description.strip()) <= 1500:
        raise ValueError("invalid_prompt")
    return (
        "Edit the supplied reference photograph(s). Preserve each person's identity, face geometry, "
        "skin tone, age, body proportions and expression. Preserve everything except the requested changes. "
        "Create a photorealistic, fully clothed, non-deceptive result. "
        + PRESETS[preset].instruction
        + "\nUser's requested change: "
        + description.strip()
    )
