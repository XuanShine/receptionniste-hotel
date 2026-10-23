"""Couches du prompt vocal : noyau stable, fiche opérationnelle, savoir-faire à la demande."""

import asyncio
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import agent  # noqa: E402
import knowledge  # noqa: E402
import provision_sip  # noqa: E402

DATA = SRC / "data"
INSTRUCTIONS = (SRC / "instructions.md").read_text(encoding="utf-8")

TOOL_NAMES = [
    "get_hotel_policy",
    "get_rooms",
    "get_mucuna_menu",
    "get_nearby_restaurants",
    "get_special_occasions",
    "get_emergency_procedure",
    "get_message_taking",
    "get_grasse_guide",
]

# Phrases factuelles du prompt d'origine. Elles doivent survivre dans une fiche.
KEPT_FACTS = [
    "Nom : Hôtel Panorama",
    "Adresse : 2 Cours Honoré Cresp, 06130 Grasse, France",
    "Téléphone : +33 4 93 36 80 80",
    "Site : https://panoramagrasse.com/",
    "Seul hôtel du centre-ville de Grasse",
    "Fête du Jasmin, Expo Rose, rallyes…",
    "Palais des Congrès",
    "charme provençal",
    "baie de Cannes et la mer Méditerranée",
    "doubles (1 lit double), triples (3 lits simples)",
    "jusqu’à 4 personnes",
    "balcon privé",
    "climatisation réversible",
    "minibar et sont insonorisées",
    "Ascenseur dans l’hôtel",
    "pas de prix fixes",
    "Tarif réduit pour une personne seule en chambre double",
    "Wi-Fi gratuit dans tout l’hôtel",
    "7h00-10h00, 10 € par adulte (environ 7 € enfant selon sources)",
    "5 € à 8 € par animal et par nuit",
    "site officiel indique souvent 5 € ou 8 €",
    "Parking au pied de l’hôtel : 10 €/nuit",
    "Parking Indigo (partenaire, très proche) : 7 € par sortie",
    "Place du Cours (≈ 20 m)",
    "Parking gratuit souvent complet",
    "Bagagerie gratuite",
    "Établissement non-fumeur",
    "Label Accueil Vélo",
    "fauteuil roulant (zones communes)",
    "ouverture prévue en juin 2027",
    "nombreux restaurants à proximité",
    "français, anglais, italien",
    "Ouvert toute l’année",
    "de 14h00 à 21h00",
    "prévenir impérativement à l’avance",
    "au plus tard 11h00",
    "capitale mondiale du parfum",
    "patrimoine immatériel de l’UNESCO",
    "Musée International de la Parfumerie (MIP) – 2 Boulevard du Jeu de Ballon",
    "jusqu’à 19h en juillet-août",
    "Cathédrale Notre-Dame-du-Puy",
    "Villa Fragonard",
    "20 Boulevard Fragonard",
    "Molinard : 60 Boulevard Victor Hugo",
    "73 Route de Cannes",
    "5 Route de Pégomas",
    "Jardin des Plantes",
    "Nice Côte d’Azur ≈ 25-30 min",
    "Bienvenue à l’Hôtel Panorama, capital du parfum !",
    "hotelpanoramagrasse@gmail.com",
    "04 93 36 80 80",
]


def _corpus() -> str:
    parts = [INSTRUCTIONS]
    parts.extend(path.read_text(encoding="utf-8") for path in sorted(DATA.glob("*.md")))
    return "\n".join(parts)


def test_core_prompt_stays_short_and_names_every_tool():
    lines = INSTRUCTIONS.splitlines()
    assert len(lines) <= 50
    assert "Véro" in INSTRUCTIONS
    assert "Hôtel Panorama" in INSTRUCTIONS
    assert "Grasse" in INSTRUCTIONS
    for name in TOOL_NAMES:
        assert name in INSTRUCTIONS
    assert "humain" in INSTRUCTIONS
    assert "get_hotel_policy" in INSTRUCTIONS


def test_original_facts_are_still_in_the_layers():
    corpus = _corpus()
    missing = [fact for fact in KEPT_FACTS if fact not in corpus]
    assert missing == []


def test_operations_sheet_has_hours_rates_and_cancellation_handoff():
    text = (DATA / "operations.md").read_text(encoding="utf-8")
    assert "14:00" in text
    assert "14h00" in text
    lowered = text.lower()
    assert "annulation" in lowered
    assert "transférer à un humain" in lowered
    assert "pas de prix fixes" in text
    for invented in ("non remboursable", "48 heures", "72 heures", "sans frais", "acompte"):
        assert invented not in lowered


def test_missing_know_how_is_a_placeholder_not_an_invented_fact():
    mucuna = (DATA / "mucuna.md").read_text(encoding="utf-8")
    assert mucuna.startswith("# Menus du Mucuna")
    assert "PLACEHOLDER" in mucuna
    assert "€" not in mucuna
    for name in ("restaurants.md", "special_occasions.md", "emergencies.md", "messages.md"):
        sheet = (DATA / name).read_text(encoding="utf-8")
        assert "PLACEHOLDER" in sheet


def test_tools_read_their_sheet_on_demand(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "DATA_DIR", tmp_path)
    (tmp_path / "operations.md").write_text("check-in 14:00 UNIQUE", encoding="utf-8")
    first = asyncio.run(knowledge.get_hotel_policy())
    assert first == "check-in 14:00 UNIQUE"
    (tmp_path / "operations.md").write_text("fiche mise à jour", encoding="utf-8")
    assert asyncio.run(knowledge.get_hotel_policy()) == "fiche mise à jour"


def test_each_tool_returns_its_real_sheet():
    expected = {
        "get_hotel_policy": "operations.md",
        "get_rooms": "rooms.md",
        "get_mucuna_menu": "mucuna.md",
        "get_nearby_restaurants": "restaurants.md",
        "get_special_occasions": "special_occasions.md",
        "get_emergency_procedure": "emergencies.md",
        "get_message_taking": "messages.md",
        "get_grasse_guide": "grasse.md",
    }
    by_name = {tool.info.name: tool for tool in knowledge.KNOWLEDGE_TOOLS}
    assert list(by_name) == TOOL_NAMES
    for name, filename in expected.items():
        body = asyncio.run(by_name[name]())
        assert body == (DATA / filename).read_text(encoding="utf-8")
        assert by_name[name].info.description


def test_missing_sheet_stays_a_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "DATA_DIR", tmp_path)
    text = knowledge.read_sheet("operations.md")
    assert text.startswith("PLACEHOLDER")
    assert "Ne rien inventer" in text
    assert knowledge.read_sheet("../instructions.md").startswith("PLACEHOLDER")


def test_agent_declares_tools_for_the_realtime_session_update():
    receptionist = agent.Receptionniste()
    assert receptionist.instructions == INSTRUCTIONS
    tools = receptionist.tools
    assert [tool.info.name for tool in tools] == TOOL_NAMES

    from livekit.agents.llm.utils import build_legacy_openai_schema

    for tool in tools:
        schema = build_legacy_openai_schema(tool, internally_tagged=True)
        assert schema["type"] == "function"
        assert schema["name"] == tool.info.name
        assert schema["description"]
        assert schema["parameters"]["type"] == "object"
        assert schema["parameters"].get("properties", {}) == {}


def test_sip_entrypoint_behaviour_is_unchanged():
    source = (SRC / "agent.py").read_text(encoding="utf-8")
    assert "xai.realtime.RealtimeModel(voice=GROK_VOICE)" in source
    assert "Accueille l'appelant en français, brièvement" in source
    assert "et demande comment tu peux l'aider." in source
    assert provision_sip.ROOM_PREFIX == "call-"
    assert callable(provision_sip.sip_host)
    assert callable(provision_sip.main)
