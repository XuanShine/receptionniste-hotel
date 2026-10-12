"""Politique de codecs, sans pile SIP."""

from reception.codecs import apply_codec_policy, codec_priority


def test_french_telephony_order():
    assert codec_priority("PCMA/8000/1") > codec_priority("PCMU/8000/1")
    assert codec_priority("PCMU/8000/1") > codec_priority("G722/16000/1")
    assert codec_priority("G722/16000/1") > 0


def test_opus_and_speex_are_disabled_in_v1():
    assert codec_priority("opus/48000/2") == 0
    assert codec_priority("speex/16000/1") == 0
    assert codec_priority("iLBC/8000/1") == 0


def test_apply_writes_every_offered_codec():
    written: list[tuple[str, int]] = []

    def set_priority(codec_id: str, priority: int) -> None:
        written.append((codec_id, priority))

    offered = ["speex/8000/1", "PCMA/8000/1", "PCMU/8000/1"]
    applied = apply_codec_policy(set_priority, offered)
    assert applied == written
    assert ("PCMA/8000/1", 255) in applied
    assert ("speex/8000/1", 0) in applied
