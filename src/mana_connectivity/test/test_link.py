"""Alert link: heartbeats, the Wi-Fi and Bluetooth rule, and the outbox (pure Python)."""

import re
from pathlib import Path

from mana_connectivity.link import MAX_MISSED, Heartbeat, Link, LinkSelector, Outbox

DT = 0.1


def fade(selector, wifi_up_at, seconds, bluetooth_up=True):
    """Run at 10 Hz; wifi_up_at(t) says whether Wi-Fi answers at t. Returns the links chosen."""
    wifi, bluetooth, chosen = Heartbeat(), Heartbeat(), []
    for i in range(round(seconds / DT)):
        t = round(i * DT, 2)
        if wifi_up_at(t):
            wifi.beat(t)
        if bluetooth_up:
            bluetooth.beat(t)
        chosen.append(selector.update(wifi.missed(t), bluetooth.missed(t), t))
    return chosen


def switches(chosen):
    return [b for a, b in zip(chosen, chosen[1:], strict=False) if a != b]


def test_heartbeats_count_the_ones_missed_in_a_row():
    beat = Heartbeat(period_s=1.0)
    assert beat.missed(5.0) == MAX_MISSED  # never answered
    beat.beat(10.0)
    # a beat counts as missed half a period after it was due
    assert [beat.missed(t) for t in (10.5, 11.0, 11.6, 13.2)] == [0, 0, 1, 2]


def test_steady_wifi_carries_the_alerts():
    assert set(fade(LinkSelector(), lambda t: True, 10)) == {Link.WIFI}


def test_losing_wifi_moves_the_alerts_to_bluetooth():
    chosen = fade(LinkSelector(), lambda t: t < 3.0, 10)
    assert switches(chosen) == [Link.BLUETOOTH]


def test_a_fading_signal_gives_one_switch_away_and_one_back():
    # Wi-Fi drops out for 3 s at a time, comes back for 2 s, then holds from 20 s
    def wifi(t):
        return t < 2.0 or t >= 20.0 or (t % 5.0) >= 3.0

    assert switches(fade(LinkSelector(), wifi, 30)) == [Link.BLUETOOTH, Link.WIFI]


def test_wifi_must_be_steady_for_back_s_before_alerts_return():
    chosen = fade(LinkSelector(back_s=5.0), lambda t: not 2.0 <= t < 5.0, 12)
    back = next(i for i, link in enumerate(chosen) if i > 50 and link == Link.WIFI)
    assert back * DT >= 5.0 + 5.0 - DT


def test_with_neither_radio_there_is_no_link_until_one_answers():
    s = LinkSelector()
    assert s.update(MAX_MISSED, MAX_MISSED, 0.0) is None
    assert s.update(MAX_MISSED, 0, 0.1) == Link.BLUETOOTH
    assert s.update(MAX_MISSED, MAX_MISSED, 0.2) is None


def test_an_alert_is_resent_until_acknowledged():
    box = Outbox(resend_s=1.0)
    alert = box.add("Drop ahead, stopping")
    assert box.due(Link.WIFI, 0.0) == [(alert, "Drop ahead, stopping")]
    assert box.due(Link.WIFI, 0.5) == []
    assert box.due(Link.WIFI, 1.0) == [(alert, "Drop ahead, stopping")]
    box.ack(alert)
    assert box.due(Link.WIFI, 5.0) == []


def test_a_switch_sends_unacknowledged_alerts_over_the_new_link_at_once():
    box = Outbox(resend_s=10.0)
    first = box.add("Obstacle ahead, stopping")
    box.due(Link.WIFI, 0.0)
    second = box.add("Stopped by you")
    assert box.due(Link.BLUETOOTH, 0.1) == [
        (first, "Obstacle ahead, stopping"),
        (second, "Stopped by you"),
    ]


def test_without_a_link_alerts_wait_and_none_is_lost():
    box = Outbox()
    alert = box.add("Drop ahead, stopping")
    assert box.due(None, 0.0) == []
    assert box.due(Link.BLUETOOTH, 3.0) == [(alert, "Drop ahead, stopping")]


def test_codes_match_the_message_type():
    msg = Path(__file__).parents[2] / "mana_msgs/msg/LinkReport.msg"
    codes = dict(re.findall(r"^uint8 (\w+)=(\d+)", msg.read_text(), re.M))
    assert {k: int(v) for k, v in codes.items()} == {e.name: e.value for e in Link}


def test_one_lost_beat_and_a_late_one_do_not_switch():
    beat, s = Heartbeat(1.0), LinkSelector()
    s.update(0, 0, 9.0)
    beat.beat(10.0)  # the beat due at 11.0 is lost; the next comes late, at 12.05
    assert s.update(beat.missed(12.0), 0, 12.0) == Link.WIFI


def test_at_start_the_first_radio_to_answer_takes_the_alerts_at_once():
    s = LinkSelector()
    assert s.update(MAX_MISSED, MAX_MISSED, 0.0) is None
    assert s.update(0, 0, 0.1) == Link.WIFI


def test_wifi_takes_the_alerts_at_once_when_bluetooth_is_down_too():
    s = LinkSelector()
    s.update(0, 0, 0.0)
    assert s.update(MAX_MISSED, MAX_MISSED, 1.0) is None
    assert s.update(0, MAX_MISSED, 1.1) == Link.WIFI  # no hold: nothing else carries them


def test_alert_ids_differ_between_boots_so_a_late_answer_cannot_clear_a_new_alert():
    old, new = Outbox(boot="aaaaaa"), Outbox(boot="bbbbbb")
    stale = old.add("Stopped by you")
    fresh = new.add("Drop ahead, stopping")
    new.ack(stale)
    assert stale != fresh and fresh in new.pending


def test_the_oldest_unanswered_alert_is_dropped_past_the_cap():
    box = Outbox(max_pending=2)
    first = box.add("one")
    box.add("two")
    box.add("three")
    assert first not in box.pending and len(box.pending) == 2 and box.dropped == 1


def test_heartbeats_are_counted_in_periods_of_the_heartbeat():
    beat = Heartbeat(period_s=0.5)
    beat.beat(10.0)
    assert [beat.missed(t) for t in (10.2, 10.8, 11.3)] == [0, 1, 2]


def test_the_cap_is_the_largest_count_the_message_can_carry():
    msg = Path(__file__).parents[2] / "mana_msgs/msg/LinkReport.msg"
    assert re.search(r"^uint8 missed_heartbeats", msg.read_text(), re.M)
    assert MAX_MISSED == 2**8 - 1


def test_a_radio_that_missed_missed_to_leave_beats_cannot_carry_alerts():
    s = LinkSelector(missed_to_leave=2)
    assert s.update(2, 1, 0.0) == Link.BLUETOOTH
    assert LinkSelector(missed_to_leave=2).update(2, 2, 0.0) is None


def test_wifi_steady_for_exactly_back_s_takes_the_alerts_back():
    s = LinkSelector(back_s=5.0)
    assert s.update(5, 0, 0.0) == Link.BLUETOOTH
    assert s.update(0, 0, 1.0) == Link.BLUETOOTH  # steady from here
    assert s.update(0, 0, 5.5) == Link.BLUETOOTH
    assert s.update(0, 0, 6.0) == Link.WIFI


def test_alert_ids_count_up_from_one_after_the_boot_id():
    box = Outbox(boot="b")
    assert [box.add("a"), box.add("b"), box.add("c")] == ["b-1", "b-2", "b-3"]
    assert re.fullmatch(r"[0-9a-f]{6}", Outbox().boot)  # a random boot id when none is given


def test_an_alert_is_not_resent_over_the_same_link_before_resend_s():
    box = Outbox(resend_s=1.0, boot="b")
    box.add("Drop ahead, stopping")
    assert [i for i, _ in box.due(Link.WIFI, 10.0)] == ["b-1"]  # not 0 s, which equals Link.WIFI
    assert box.due(Link.WIFI, 10.5) == []
    assert [i for i, _ in box.due(Link.WIFI, 11.0)] == ["b-1"]


def test_every_alert_dropped_past_the_cap_is_counted():
    box = Outbox(max_pending=2, boot="b")
    for text in "abcd":
        box.add(text)
    assert (list(box.pending), box.dropped) == (["b-3", "b-4"], 2)
