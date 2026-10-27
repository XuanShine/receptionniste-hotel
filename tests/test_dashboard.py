import sys
import unittest
from datetime import date
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

QUOTE = '18:47:03  Outil verifier_disponibilite_et_prix — {"chambres": []} « {} »\n'


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

    def test_main_list_hides_deleted_calls_and_the_delete_badge(self) -> None:
        kept = desk.parse_call(
            HEADER
            + "18:50:20  Outil prendre_message — Message enregistré. "
            + '« {"motif": "Bloqué dehors #URGENT"} »\n'
            + "\nRésumé : Le client attend #suite.\n",
            Path("visible.txt"),
        )
        removed = desk.parse_call(
            HEADER.replace("18:46:42", "19:10:00") + "\nRésumé : Déjà traité #rappel.\n#delete\n",
            Path("supprime.txt"),
        )
        assert kept is not None and removed is not None
        self.assertEqual(
            dashboard._chips(kept),
            [
                ("message", "accent"),
                ("#URGENT", "primary"),
                ("#suite", "primary"),
            ],
        )
        self.assertNotIn("#delete", [label for label, _color in dashboard._chips(kept)])
        self.assertEqual(dashboard._chips(removed), [("#rappel", "primary")])
        board = desk.DayDesk(
            desk.Span.of(date(2026, 10, 20), date(2026, 10, 20)),
            (removed, kept),
            (),
        )
        self.assertEqual(dashboard.listed_calls(board, "appels"), (kept,))
        self.assertEqual(dashboard.listed_calls(board, "poubelle"), (removed,))

    def test_poubelle_lists_only_deleted_calls(self) -> None:
        quiet = desk.parse_call(HEADER + "\nRésumé : Rien à signaler.\n", Path("calme.txt"))
        trashed = desk.parse_call(
            HEADER.replace("18:46:42", "19:12:00") + QUOTE + "#DELETE\n",
            Path("poubelle.txt"),
        )
        assert quiet is not None and trashed is not None
        board = desk.DayDesk(
            desk.Span.of(date(2026, 10, 20), date(2026, 10, 20)),
            (trashed, quiet),
            (),
        )
        self.assertEqual(dashboard.listed_calls(board, "poubelle"), (trashed,))
        self.assertEqual(dashboard.listed_calls(board, "appels"), (quiet,))
        self.assertTrue(desk.is_deleted(trashed))
        self.assertFalse(desk.is_deleted(quiet))


if __name__ == "__main__":
    unittest.main()
