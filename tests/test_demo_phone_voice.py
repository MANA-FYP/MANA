"""The phone page says MANA's lines itself and warns when the server stops answering: a blind user
walking with the phone has no laptop to hear (review of real-world readiness, 25 November). The
page is plain JavaScript run by the phone's browser, so its rules are checked in its source."""

from pathlib import Path

JS = (Path(__file__).parents[1] / "demo" / "web" / "phone.js").read_text(encoding="utf-8")


def test_each_new_line_is_said_once_on_the_phone_and_a_stop_cuts_in():
    assert "sayAloud(m.heard);" in JS  # every state from the server
    assert "e.n > lastSpoken && e.speak" in JS  # only new lines, each once
    assert "if (lastSpoken === null) { lastSpoken = newest; return; }" in JS  # not the backlog
    assert 'if (priority === "URGENT") speechSynthesis.cancel();' in JS


def test_a_stop_and_a_warning_also_vibrate():
    assert "URGENT: [400, 100, 400], WARNING: [200]" in JS
    assert "navigator.vibrate(BUZZ[priority])" in JS


def test_speech_is_allowed_by_the_start_tap():
    start = JS[JS.index('getElementById("startBtn").onclick') :]
    assert start.index("voiceReady = true;") < start.index("await startAll();")


def test_the_phone_warns_by_itself_when_the_server_goes_quiet_and_when_it_is_back():
    assert "const LINK_LOST_MS = 2000;" in JS
    assert 'speakNow("Connection lost. Stop and wait.", "URGENT");' in JS
    assert "linkLost();" in JS[JS.index("ws.onclose") :]  # the socket closing, too
    assert 'speakNow("Connected again.", "INFO");' in JS


def test_after_a_reconnect_or_a_server_restart_the_phone_speaks_again_without_a_burst():
    """A server started again numbers its lines from 1, and the page kept the old count: before,
    it said nothing, stops included, until the new count passed the old one. After a short drop
    it said every line it had missed at once."""
    assert "resync = true;" in JS[JS.index("ws.onopen") : JS.index("ws.onclose")]
    say = JS[JS.index("function sayAloud") :]
    assert "if (resync || newest < lastSpoken) {" in say
    reset = say[say.index("if (resync || newest < lastSpoken) {") :]
    assert reset.index("lastSpoken = newest;") < reset.index("return;")  # the count starts again
    assert 'now.priority === "URGENT") speakNow(now.text, now.priority);' in reset  # a stop is said
