from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    label: str
    instruction: str
    inputs: int = 1
    credits: int = 1


PRESETS = {
    "reference": Preset("Фото по примеру", "Image 1 is the user; image 2 is a staging reference.", 2, 1),
    "hair": Preset("Причёска", "Change only the hairstyle and hair color as requested."),
    "clothes": Preset("Одежда", "Change only the clothing as requested; keep the person fully clothed."),
    "glasses": Preset("Очки", "Add or change eyeglasses as requested, with realistic fit and reflections."),
    "background": Preset("Фон", "Change only the background and harmonize lighting as requested."),
    "enhance": Preset(
        "Улучшение фото", "Reduce noise and improve natural lighting without inventing identity details."
    ),
    "merge": Preset("Объединить два фото", "Combine both reference images in one natural composition.", 2, 1),
    "document_original": Preset(
        "Фото на документы · исходник", "Prepare the original photograph for printing by cropping and resizing only."
    ),
    "document": Preset(
        "Фото на документы",
        "Create exactly one natural, photorealistic, fully clothed head-and-shoulders portrait. "
        "Preserve the person's identity, face geometry, facial features, skin texture, skin tone, age "
        "and body proportions. Do not beautify, smooth skin, reshape the face or invent identity details. "
        "Use a frontal pose facing the camera, a natural neutral expression, even lighting and a plain "
        "white background. Center the person in a vertical 7:9 composition with enough whitespace "
        "above the head and around the shoulders for a central crop. Preserve the original clothes "
        "unless the user explicitly requests a neutral light shirt or a business suit. Do not infer "
        "gender or change gender presentation. Generate one portrait only: no duplicate portraits, "
        "contact sheet, text, logos, watermark or decorations. These constraints apply to the user's "
        "description. This is a questionnaire portrait, not an officially accepted passport photograph."
    ),
}

# Legacy sandbox aliases follow the current catalog; historical invoices keep their amounts.
PACKS = {"small": (20, 24000), "medium": (50, 48000), "large": (75, 72000)}


def prompt_for(preset: str, description: str) -> str:
    if preset not in PRESETS or not 1 <= len(description.strip()) <= 1500:
        raise ValueError("invalid_prompt")
    if preset == "document":
        return PRESETS[preset].instruction + "\nUser's requested clothing choice: " + description.strip()
    if preset == "reference":
        return "Create a natural photographic portrait using the user in Image 1. " + description.strip()
    return (
        "Edit the supplied reference photograph(s). Preserve each person's identity, face geometry, "
        "skin tone, age, body proportions and expression. Preserve everything except the requested changes. "
        "Create a photorealistic, fully clothed, non-deceptive result. "
        + PRESETS[preset].instruction
        + "\nUser's requested change: "
        + description.strip()
    )
