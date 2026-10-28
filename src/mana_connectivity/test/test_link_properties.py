"""Property-based tests (#83) of the alert link: while Bluetooth answers, one Wi-Fi outage gives
at most one switch to Bluetooth and one back, and Wi-Fi that keeps answering is never left."""

from hypothesis import given, settings
from hypothesis import strategies as st
from mana_connectivity.link import Link, LinkSelector

DT = 0.1
PROPERTY = settings(max_examples=300, deadline=None, derandomize=True)


def chosen(wifi_missed) -> list[Link | None]:
    s, out, t = LinkSelector(), [], 0.0
    for missed in wifi_missed:
        link = s.update(missed, 0, t)
        if not out or out[-1] != link:
            out.append(link)
        t = round(t + DT, 3)
    return out


@PROPERTY
@given(st.integers(0, 50), st.integers(1, 100), st.integers(0, 120), st.integers(2, 9))
def test_one_wifi_outage_gives_at_most_one_switch_each_way(before, outage, after, missed):
    links = chosen([0] * before + [missed] * outage + [0] * after)
    assert links in (
        [Link.WIFI],
        [Link.BLUETOOTH],
        [Link.WIFI, Link.BLUETOOTH],
        [Link.BLUETOOTH, Link.WIFI],
        [Link.WIFI, Link.BLUETOOTH, Link.WIFI],
    )
    if after * DT >= LinkSelector().back_s + 2 * DT:  # one tick of margin
        assert links[-1] == Link.WIFI  # steady again for back_s: Wi-Fi carries the alerts


@PROPERTY
@given(st.lists(st.integers(0, 1), min_size=1, max_size=300))
def test_wifi_that_keeps_answering_is_never_left(wifi_missed):
    assert chosen(wifi_missed) == [Link.WIFI]
