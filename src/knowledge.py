"""Fiches lues à la demande. Le prompt stable ne les contient pas."""

from pathlib import Path

from livekit.agents import function_tool

DATA_DIR = Path(__file__).resolve().parent / "data"

_MISSING = (
    "PLACEHOLDER : cette fiche n'est pas dans le dépôt. "
    "Ne rien inventer. Proposer la réception au 04 93 36 80 80 "
    "ou par email hotelpanoramagrasse@gmail.com."
)


def read_sheet(filename: str) -> str:
    root = DATA_DIR.resolve()
    path = (DATA_DIR / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return _MISSING
    if not path.is_file():
        return _MISSING
    return path.read_text(encoding="utf-8")


@function_tool(
    description=(
        "À appeler quand l'appelant demande les horaires, le check-in, le check-out, "
        "une annulation, les conditions d'annulation, les tarifs de base ou une question "
        "fréquente (petit-déjeuner, animaux, parking, wifi, bagagerie, accès, adresse, "
        "langues). Aucun argument. La fiche fait foi. Pour une annulation, la transférer "
        "à un humain : ne pas inventer les conditions."
    ),
)
async def get_hotel_policy() -> str:
    """Lit la fiche opérationnelle de l'hôtel."""
    return read_sheet("operations.md")


@function_tool(
    description=(
        "À appeler quand l'appelant demande les chambres, les lits, le balcon ou les "
        "équipements. Aucun argument. Ne pas inventer de prix : la fiche dit comment orienter."
    ),
)
async def get_rooms() -> str:
    """Lit la fiche des chambres."""
    return read_sheet("rooms.md")


@function_tool(
    description=(
        "À appeler quand l'appelant demande le Mucuna, sa carte ou ses menus. "
        "Aucun argument. Si la fiche est un placeholder, dire que la carte n'est pas "
        "disponible et n'inventer aucun plat, prix ou horaire."
    ),
)
async def get_mucuna_menu() -> str:
    """Lit la fiche des menus du Mucuna."""
    return read_sheet("mucuna.md")


@function_tool(
    description=(
        "À appeler quand l'appelant demande où manger ou quels restaurants sont à "
        "proximité. Aucun argument. Ne pas inventer de nom de restaurant absent de la fiche."
    ),
)
async def get_nearby_restaurants() -> str:
    """Lit la fiche des restaurants à proximité."""
    return read_sheet("restaurants.md")


@function_tool(
    description=(
        "À appeler pour une fête, un événement à Grasse, ou une occasion spéciale. "
        "Aucun argument. Ne pas inventer d'offre ni de prix absents de la fiche."
    ),
)
async def get_special_occasions() -> str:
    """Lit la fiche des occasions spéciales."""
    return read_sheet("special_occasions.md")


@function_tool(
    description=(
        "À appeler pour une urgence, un malaise, un incident de sécurité ou un danger. "
        "Aucun argument. Suivre la fiche. Ne pas inventer de geste."
    ),
)
async def get_emergency_procedure() -> str:
    """Lit la fiche d'urgence."""
    return read_sheet("emergencies.md")


@function_tool(
    description=(
        "À appeler quand l'appelant veut laisser un message pour un client ou pour la "
        "réception. Aucun argument. Ne pas promettre une transmission que la fiche n'écrit pas."
    ),
)
async def get_message_taking() -> str:
    """Lit la fiche de prise de message."""
    return read_sheet("messages.md")


@function_tool(
    description=(
        "À appeler pour Grasse, les parfumeries, les musées ou les alentours. "
        "Aucun argument. Ne pas ajouter d'horaire ou de distance absents de la fiche. "
        "L'appeler avant de proposer une visite."
    ),
)
async def get_grasse_guide() -> str:
    """Lit la fiche Grasse et alentours."""
    return read_sheet("grasse.md")


# Ordre envoyé dans le session.update du realtime Grok Voice.
KNOWLEDGE_TOOLS = [
    get_hotel_policy,
    get_rooms,
    get_mucuna_menu,
    get_nearby_restaurants,
    get_special_occasions,
    get_emergency_procedure,
    get_message_taking,
    get_grasse_guide,
]
