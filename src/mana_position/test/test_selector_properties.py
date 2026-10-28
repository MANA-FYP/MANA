"""Property-based tests (#83) of the position source: for any outage, one outage gives at most
one switch away from GPS and one back, and an outage shorter than out_s gives none."""

from hypothesis import given, settings
from hypothesis import strategies as st
from mana_position.selector import PositionSelector, Source

DT = 0.1
PROPERTY = settings(max_examples=300, deadline=None, derandomize=True)
good = st.floats(0.0, 7.9)  # better than back_m (8 m)
bad = st.one_of(st.none(), st.floats(15.01, 100.0))  # worse than out_m (15 m), or no fix


def switches(errors) -> list[Source]:
    s, out, t = PositionSelector(), [], 0.0
    for error in errors:
        source = s.update(error, True, t)
        if not out or out[-1] != source:
            out.append(source)
        t = round(t + DT, 3)
    return out


@PROPERTY
@given(st.integers(0, 50), st.integers(1, 100), st.integers(0, 120), good, bad)
def test_one_outage_gives_at_most_one_switch_away_and_one_back(before, outage, after, ok, lost):
    errors = [ok] * before + [lost] * outage + [ok] * after
    sources = switches(errors)
    assert sources in (
        [Source.GPS],
        [Source.GPS, Source.LIDAR_MAP],
        [Source.GPS, Source.LIDAR_MAP, Source.GPS],
    )
    s = PositionSelector()
    if outage * DT < s.out_s:
        assert sources == [Source.GPS]  # a short outage never switches
    # one tick of margin on each: the switch comes on the tick where the time has passed
    if outage * DT >= s.out_s + 2 * DT and after * DT >= s.back_s + 2 * DT:
        assert sources == [Source.GPS, Source.LIDAR_MAP, Source.GPS]


@PROPERTY
@given(st.lists(st.one_of(good, st.floats(8.0, 15.0)), min_size=1, max_size=300))
def test_an_error_between_the_two_levels_never_switches(errors):
    assert switches(errors) == [Source.GPS]
