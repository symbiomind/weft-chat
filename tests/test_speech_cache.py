"""
Tests for app/speech_cache.py: spoken clips kept so a reply can be heard again.

The assertions that would matter:

* ``test_a_clip_is_only_found_in_its_owners_folder`` — the key is the voice and the
  words, which two people can share. The folder is what keeps one user's clip from
  being served to another.
* ``test_the_key_is_every_part_of_the_voice`` — a clip made by one voice must never be
  played for another.
* ``test_turning_the_cache_off_clears_it`` — 0 means nothing stays on the server.
"""

import os
import time

import pytest

from app import speech_cache

ALICE = "11111111-aaaa-4aaa-8aaa-111111111111"
BOB = "22222222-bbbb-4bbb-8bbb-222222222222"


def key(**changes):
    parts = {
        "provider_id": "p1", "base_url": "http://kokoro.lan/v1",
        "model": "kokoro", "voice": "af_bella", "text": "Hello.",
    }
    parts.update(changes)
    return speech_cache.key_for(**parts)


@pytest.fixture(autouse=True)
def fresh_sweeps():
    speech_cache._last_swept.clear()


def test_a_kept_clip_comes_back_with_its_type():
    speech_cache.keep(ALICE, key(), "audio/mpeg", b"ID3audio")

    assert speech_cache.fetch(ALICE, key()) == ("audio/mpeg", b"ID3audio")


def test_a_clip_is_only_found_in_its_owners_folder():
    speech_cache.keep(ALICE, key(), "audio/mpeg", b"ID3audio")

    assert speech_cache.fetch(BOB, key()) is None


@pytest.mark.parametrize("field", ["provider_id", "base_url", "model", "voice", "text"])
def test_the_key_is_every_part_of_the_voice(field):
    assert key(**{field: "something else"}) != key()


def test_fields_cannot_run_into_each_other():
    assert key(model="ab", voice="c") != key(model="a", voice="bc")


def test_an_unknown_audio_type_is_played_but_not_kept():
    speech_cache.keep(ALICE, key(), "audio/x-something", b"bytes")

    assert speech_cache.fetch(ALICE, key()) is None


def test_wav_is_kept_as_wav():
    speech_cache.keep(ALICE, key(), "audio/x-wav", b"RIFFaudio")

    assert speech_cache.fetch(ALICE, key()) == ("audio/wav", b"RIFFaudio")


def test_an_expired_clip_is_a_miss_and_is_removed(monkeypatch):
    monkeypatch.setattr(speech_cache, "ttl_seconds", lambda: 3600)
    speech_cache.keep(ALICE, key(), "audio/mpeg", b"ID3audio")
    path = speech_cache.user_folder(ALICE) / f"{key()}.mp3"
    old = time.time() - 7200
    os.utime(path, (old, old))

    assert speech_cache.fetch(ALICE, key()) is None
    assert not path.exists()


def test_hearing_a_clip_keeps_it_longer(monkeypatch):
    monkeypatch.setattr(speech_cache, "ttl_seconds", lambda: 3600)
    speech_cache.keep(ALICE, key(), "audio/mpeg", b"ID3audio")
    path = speech_cache.user_folder(ALICE) / f"{key()}.mp3"
    nearly = time.time() - 3000
    os.utime(path, (nearly, nearly))

    assert speech_cache.fetch(ALICE, key()) is not None
    assert path.stat().st_mtime > time.time() - 60


def test_the_sweep_takes_only_expired_clips(monkeypatch):
    monkeypatch.setattr(speech_cache, "ttl_seconds", lambda: 3600)
    speech_cache.keep(ALICE, key(text="old"), "audio/mpeg", b"old")
    speech_cache.keep(ALICE, key(text="new"), "audio/mpeg", b"new")
    folder = speech_cache.user_folder(ALICE)
    old = time.time() - 7200
    os.utime(folder / f"{key(text='old')}.mp3", (old, old))
    stranger = folder / "notes.txt"
    stranger.write_text("not ours")
    os.utime(stranger, (old, old))

    assert speech_cache.sweep() == 1
    assert (folder / f"{key(text='new')}.mp3").exists()
    assert stranger.exists(), "a file this module did not write is not its to delete"


def test_turning_the_cache_off_clears_it(monkeypatch):
    speech_cache.keep(ALICE, key(), "audio/mpeg", b"ID3audio")
    monkeypatch.setattr(speech_cache, "ttl_seconds", lambda: 0)

    assert speech_cache.sweep() == 1
    assert speech_cache.fetch(ALICE, key()) is None


def test_a_strange_user_id_is_refused():
    with pytest.raises(ValueError):
        speech_cache.user_folder("../../etc")


def test_a_key_that_is_not_a_hash_is_never_looked_up():
    assert speech_cache.fetch(ALICE, "../../weft.db") is None
