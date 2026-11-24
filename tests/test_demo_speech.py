"""Tests for the voice helpers: the word list, parsing recogniser output, resampling audio, and
the loader returning None when the model is not there. No speech model is needed."""

import json
import struct

import pytest

from demo import speech
from demo.middleware import WORD_COMMANDS
from demo.speech import COMMAND_WORDS, CommandRecognizer, grammar, parse_result, to_pcm16_16k


def test_the_grammar_is_the_ten_command_words_the_phrase_words_and_unknown():
    words = json.loads(grammar())
    assert words[-1] == "[unk]" and len(COMMAND_WORDS) == 10
    assert set(words[:-1]) == set(COMMAND_WORDS) | set(speech.PHRASE_WORDS)
    # every word the middleware's phrases look for can be heard by the laptop's recogniser
    from demo.middleware import DESTINATION, FLOOR_WORDS, MARK_PHRASES

    needed = {w for p in MARK_PHRASES for w in p.split()} | {DESTINATION, "where", "floor"}
    assert needed | set(FLOOR_WORDS) <= set(words)


def test_a_phrase_is_a_result_with_a_word_that_is_not_a_command():
    def result(text):
        return json.dumps({"text": text})

    assert speech.phrase_of(result("floor two")) == "floor two"
    assert speech.phrase_of(result("[unk] where go")) == "(unclear) where go"
    assert speech.phrase_of(result("stop")) == speech.phrase_of(result("go go")) == ""
    assert speech.phrase_of(result("[unk]")) == speech.phrase_of(result("[unk] [unk]")) == ""
    assert speech.phrase_of("not json") == ""
    # Waqas (#196): Vosk hears "don't go", "can we go", "let's go now" as unknown words and "go"
    from demo.middleware import in_sentence

    for said in ("[unk] go", "[unk] [unk] go", "go [unk]"):
        assert in_sentence(speech.phrase_of(result(said))), said


def test_feeding_a_phrase_keeps_it_for_the_server():
    class Fake:
        def AcceptWaveform(self, data):  # noqa: N802
            return True

        def Result(self):  # noqa: N802
            return json.dumps({"text": "on the first floor", "result": [{"word": "on", "conf": 1}]})

    rec = CommandRecognizer(Fake())
    assert rec.feed(b"\x00\x00") == [("on", 1.0)] and rec.last_phrase == "on the first floor"


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
