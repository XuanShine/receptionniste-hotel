import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from datetime import datetime

from messages import message_filename, save_message

WHEN = datetime(2026, 10, 23, 7, 47)


def test_filename_is_date_time_and_number():
    assert message_filename(WHEN, "06 11 22 33 44") == "2026-10-23 07h47 - 0611223344.txt"


def test_saved_file_holds_the_message_and_never_overwrites(tmp_path):
    first = save_message(tmp_path, WHEN, "Dupont", "0611223344", "Confirmation non reçue")
    second = save_message(tmp_path, WHEN, "Dupont", "0611223344", "Rappel")
    assert "Motif : Confirmation non reçue" in first.read_text(encoding="utf-8")
    assert first != second and second.exists()
