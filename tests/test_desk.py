import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import desk

HEADER = """\
Hôtel Panorama
Début : 2026-10-20 18:46:42
Salle : call-abc
Appelant : +33651216491
Mode : appel

"""

BOOKED = (
    '18:47:03  Outil verifier_disponibilite_et_prix — {"chambres": []} « {} »\n'
    "18:48:17  Outil creer_reservation — "
    '{"reservation": "1792514897", "nom_chambre": "Double Balcon"} '
    '« {"nom": "Nguyen", "prenom": "Paul"} »\n'
)

QUOTE = '18:47:03  Outil verifier_disponibilite_et_prix — {"chambres": []} « {} »\n'

MESSAGE = """\
Date : 20/10/2026 18:50
Nom : Nguyen Paul
Téléphone : 0651216491
Motif : Rappel pour le petit-déjeuner
"""


def _write(folder: Path, name: str, text: str) -> None:
    (folder / name).write_text(text, encoding="utf-8")


class DeskTest(unittest.TestCase):
    def test_booked_call_keeps_code_guest_and_resume(self) -> None:
        text = (
            HEADER
            + BOOKED
            + "\nRésumé : Paul Nguyen réserve la Double Balcon, code 1792514897.\n"
        )
        card = desk.parse_call(text, Path("appel.txt"))
        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.started, datetime(2026, 10, 20, 18, 46, 42))
        self.assertEqual(card.caller, "+33651216491")
        self.assertFalse(card.console)
        self.assertEqual(card.resume, "Paul Nguyen réserve la Double Balcon, code 1792514897.")
        self.assertNotIn("Résumé", card.transcript)
        reservation = next(issue for issue in card.issues if isinstance(issue, desk.Reservation))
        self.assertEqual(reservation.code, "1792514897")
        self.assertEqual(reservation.guest, "Paul Nguyen")
        self.assertEqual(reservation.room, "Double Balcon")
        self.assertFalse(any(isinstance(issue, desk.Quote) for issue in card.issues))

    def test_quote_without_booking_stays_a_quote(self) -> None:
        card = desk.parse_call(HEADER + QUOTE, Path("devis.txt"))
        assert card is not None
        self.assertIsNone(card.resume)
        self.assertEqual(card.issues, (desk.Quote(),))

    def test_transfer_failure_and_message(self) -> None:
        text = (
            HEADER
            + "18:50:01  Outil transferer_reception — (sans résultat)\n"
            + "18:50:20  Outil prendre_message — Message enregistré.\n"
            + "18:50:30  Outil rechercher_internet — échec — délai\n"
        )
        card = desk.parse_call(text, Path("suite.txt"))
        assert card is not None
        self.assertEqual(
            card.issues,
            (
                desk.Transfer(True),
                desk.Callback(),
                desk.ToolFailure("rechercher_internet", "délai"),
            ),
        )

    def test_day_keeps_that_days_calls_and_messages_newest_first(self) -> None:
        later = HEADER.replace("18:46:42", "19:10:00") + QUOTE
        other_day = HEADER.replace("2026-10-20", "2026-10-21") + QUOTE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = root / "record"
            messages = root / "messages"
            records.mkdir()
            messages.mkdir()
            _write(
                records,
                "early.txt",
                HEADER + BOOKED + "\nRésumé : Séjour réservé.\n",
            )
            _write(records, "late.txt", later)
            _write(records, "tomorrow.txt", other_day)
            _write(records, "note.txt", "pas un appel\n")
            _write(messages, "rappel.txt", MESSAGE)
            _write(
                messages,
                "autre.txt",
                MESSAGE.replace("20/10/2026", "21/10/2026"),
            )
            day = desk.load_day(
                desk.Span.of(date(2026, 10, 20), date(2026, 10, 20)),
                records,
                messages,
            )
        self.assertEqual([call.started.hour for call in day.calls], [19, 18])
        self.assertEqual(day.reservation_count, 1)
        self.assertEqual(day.missing_resume_count, 1)
        self.assertEqual(len(day.messages), 1)
        self.assertEqual(day.messages[0].name, "Nguyen Paul")
        self.assertEqual(day.messages[0].phone, "0651216491")
        self.assertEqual(day.messages[0].reason, "Rappel pour le petit-déjeuner")

    def test_span_includes_both_days_and_orders_reversed_bounds(self) -> None:
        later = HEADER.replace("18:46:42", "19:10:00") + QUOTE
        next_day = HEADER.replace("2026-10-20", "2026-10-21") + QUOTE
        forward = desk.Span.of(date(2026, 10, 20), date(2026, 10, 21))
        self.assertEqual(desk.Span.of(date(2026, 10, 21), date(2026, 10, 20)), forward)
        self.assertEqual(forward.start, date(2026, 10, 20))
        self.assertEqual(forward.end, date(2026, 10, 21))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = root / "record"
            messages = root / "messages"
            records.mkdir()
            messages.mkdir()
            _write(
                records,
                "early.txt",
                HEADER + BOOKED + "\nRésumé : Séjour réservé.\n",
            )
            _write(records, "late.txt", later)
            _write(records, "tomorrow.txt", next_day)
            _write(messages, "rappel.txt", MESSAGE)
            _write(
                messages,
                "autre.txt",
                MESSAGE.replace("20/10/2026", "21/10/2026"),
            )
            board = desk.load_day(forward, records, messages)
        self.assertEqual(
            [call.started.date() for call in board.calls],
            [date(2026, 10, 21), date(2026, 10, 20), date(2026, 10, 20)],
        )
        self.assertEqual([call.started.hour for call in board.calls], [18, 19, 18])
        self.assertEqual(board.reservation_count, 1)
        self.assertEqual(
            [note.when.date() for note in board.messages],
            [date(2026, 10, 21), date(2026, 10, 20)],
        )
        self.assertEqual(board.messages[1].name, "Nguyen Paul")
        self.assertEqual(board.messages[1].phone, "0651216491")
        self.assertEqual(board.messages[1].reason, "Rappel pour le petit-déjeuner")

    def test_save_replaces_the_file_and_a_second_save_stays_the_same(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "appel.txt"
            path.write_text(HEADER + QUOTE, encoding="utf-8")
            edited = HEADER.replace("Appelant : +33651216491", "Appelant : +33600000000") + QUOTE
            desk.save_document(path, edited)
            self.assertEqual(desk.read_document(path), edited)
            card = desk.parse_call(desk.read_document(path), path)
            assert card is not None
            self.assertEqual(card.caller, "+33600000000")
            desk.save_document(path, edited)
            self.assertEqual(path.read_bytes(), edited.encode("utf-8"))
            missing = Path(tmp) / "absent.txt"
            with self.assertRaises(OSError):
                desk.save_document(missing, "nouveau\n")
            self.assertFalse(missing.exists())

    def test_console_call_is_marked(self) -> None:
        text = HEADER.replace("Mode : appel", "Mode : console").replace(
            "Appelant : +33651216491", "Appelant : (aucun)"
        )
        card = desk.parse_call(text, Path("console.txt"))
        assert card is not None
        self.assertTrue(card.console)
        self.assertEqual(card.caller, "(aucun)")


if __name__ == "__main__":
    unittest.main()
