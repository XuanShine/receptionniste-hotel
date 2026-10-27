import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import dashboard
import desk

HEADER = """\
Hôtel Panorama
Début : 2026-10-20 18:46:42
Salle : call-abc
Appelant : +33651216491
Mode : appel

"""


class CallChipsTest(unittest.TestCase):
    def test_hashtag_tags_follow_the_existing_chips(self) -> None:
        text = (
            HEADER
            + "18:50:20  Outil prendre_message — Message enregistré. "
            + '« {"motif": "Bloqué dehors #URGENT #rappel-nuit"} »\n'
        )
        card = desk.parse_call(text, Path("urgent.txt"))
        assert card is not None
        self.assertEqual(
            dashboard._chips(card),
            [
                ("message", "accent"),
                ("sans résumé", "grey"),
                ("#URGENT", "primary"),
                ("#rappel-nuit", "primary"),
            ],
        )

    def test_call_without_hashtag_keeps_its_chips(self) -> None:
        text = HEADER + "\nRésumé : Un appel sans étiquette.\n"
        card = desk.parse_call(text, Path("calme.txt"))
        assert card is not None
        self.assertEqual(dashboard._chips(card), [])


if __name__ == "__main__":
    unittest.main()
