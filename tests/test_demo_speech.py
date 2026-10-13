"""Tests for the voice helpers: the word list, parsing recogniser output, resampling audio, and
the loader returning None when the model is not there. No speech model is needed."""

import json
import struct

import pytest

from demo import speech
from demo.middleware import WORD_COMMANDS
from demo.speech import COMMAND_WORDS, CommandRecognizer, grammar, parse_result, to_pcm16_16k


def test_the_grammar_is_the_ten_command_words_plus_unknown():
    words = json.loads(grammar())
    assert words[-1] == "[unk]"
    assert set(words[:-1]) == set(COMMAND_WORDS)
    assert len(COMMAND_WORDS) == 10


def test_every_command_word_the_recogniser_can_return_is_understood_by_the_middleware():
    assert set(COMMAND_WORDS) - set(WORD_COMMANDS) <= {"yes", "no", "left", "right"}
    assert {"stop", "go", "on", "off", "up", "down"} <= set(WORD_COMMANDS)


def test_a_result_with_words_gives_word_and_confidence():
    text = json.dumps({"result": [{"word": "stop", "conf": 0.93}, {"word": "banana", "conf": 0.8}]})
    assert parse_result(text) == [("stop", 0.93)]


def test_a_result_with_only_text_still_gives_the_command_words():
    assert parse_result(json.dumps({"text": "go go"})) == [("go", 0.5), ("go", 0.5)]


def test_garbage_gives_nothing():
    assert parse_result("not json") == []
    assert parse_result(json.dumps({"text": ""})) == []


def test_resampling_48k_to_16k_keeps_a_third_of_the_samples():
    pcm = to_pcm16_16k([0.5] * 4800, 48000)
    assert len(pcm) == 1600 * 2
    assert set(struct.unpack("<1600h", pcm)) == {int(0.5 * 32767)}


def test_resampling_clips_to_the_16_bit_range():
    values = struct.unpack("<3h", to_pcm16_16k([2.0, -2.0, 2.0], 16000))
    assert values == (32767, -32768, 32767)


def test_no_audio_gives_no_bytes():
    assert to_pcm16_16k([], 48000) == b""
    assert to_pcm16_16k([0.1], 0) == b""


def test_the_recogniser_is_none_when_the_model_folder_is_missing():
    assert CommandRecognizer.load("no/such/model") is None


def test_the_recogniser_is_none_when_vosk_is_not_installed(monkeypatch, tmp_path):
    def refuse(name, *a, **k):
        raise ImportError(name)

    monkeypatch.setattr(speech.importlib, "import_module", refuse)
    assert CommandRecognizer.load(str(tmp_path)) is None


def test_feeding_audio_returns_words_only_when_an_utterance_finishes():
    class Fake:
        def __init__(self):
            self.calls = 0

        def AcceptWaveform(self, data):  # noqa: N802
            self.calls += 1
            return self.calls == 2

        def Result(self):  # noqa: N802
            return json.dumps({"result": [{"word": "stop", "conf": 0.9}]})

    rec = CommandRecognizer(Fake())
    assert rec.feed(b"\x00\x00") == []
    assert rec.feed(b"\x00\x00") == [("stop", 0.9)]


@pytest.mark.parametrize("word", ["stop", "go"])
def test_stop_and_go_are_in_the_grammar(word):
    assert word in json.loads(grammar())
