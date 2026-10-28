# Mutation testing (#83)

Mutation testing changes the code one small step at a time (a `<` to a `<=`, a `+` to a `-`, `True` to `False`) and runs the tests on each change, a *mutant*. A mutant the tests fail on is *killed*. One that passes them *survives*: either the tests miss a real change of behaviour, or the change does not alter behaviour at all. This shows whether the tests would catch a broken rule, not only whether they pass: in #69 a new test still passed with its fix removed.

To run it again (in WSL, with mutmut 2.5.1, on a copy so that mutmut's files stay out of the repository):

```bash
python3 -m mutmut run --paths-to-mutate src/mana_core/mana_core/decision.py \
  --runner "python3 -m pytest -q -x -p no:launch_testing -p no:launch_ros tests/test_decision.py tests/test_messages.py tests/test_scenarios.py"
python3 -m mutmut results
```

For `selector.py` and `link.py` the runner is that package's `test_selector*.py` or `test_link*.py`, without the ROS node tests.

## Score

| Module | Mutants | Killed | Survived | Score |
|---|---|---|---|---|
| `mana_core/decision.py` | 347 | 293 | 54 | 84.4% |
| `mana_core/messages.py` | 169 | 154 | 15 | 91.1% |
| `mana_position/selector.py` | SELECTOR_TOTAL | SELECTOR_KILLED | SELECTOR_SURVIVED | SELECTOR_SCORE |
| `mana_connectivity/link.py` | LINK_TOTAL | LINK_KILLED | LINK_SURVIVED | LINK_SCORE |

Before this work the first run left 70 survivors in `decision.py`, 31 in `messages.py`, 5 in `selector.py` and 21 in `link.py`. Every survivor that changed behaviour now has a test. They were gaps in the tests, not bugs in the code: no test would have noticed if

- the slip, "hold tracks" or "no ground" branch had stopped the chair instead of slowing it, since none checked the stop flag;
- a yes heard before the core's first tick had approved stairs nobody had asked about;
- a stop on the very first tick, or manual mode chosen before WHEELS or TRACKS, had sent no mode at all;
- the core had crashed on a tick before any IMU report;
- the queue had said a stop direction again every 6 s while the chair waited at the same drop, or had let one decision push out a waiting direction late in a run;
- an alert had been resent over the same link before `resend_s`, alert ids had not counted up from one, or the count of alerts dropped past the cap had been wrong;
- a limit (`edge_m`, `obstacle_stop_m`, `step_m`, `min_trust`, `max_age_s`, `out_m`, `back_m`) had acted just past its value: each was tested on either side, never at it.

## The survivors, and why each is harmless

Every one of these was read, and none changes what MANA decides, says or sends.

### `decision.py` (54)

| Mutants | What changes | Why the behaviour does not |
|---|---|---|
| 75, 79, 83, 91, 94, 98, 126 | `@dataclass(frozen=True)` becomes `frozen=False` | Nothing changes a report, a limit or a decision after it is made. Frozen guards against a future bug, which no test of behaviour can see. |
| 78, 81, 82, 90, 93, 96, 97 | the default of a report field (`step_height_m`, `label`, `wheel_slip`, `error_m`, `missed_heartbeats`) | The core node fills every field from the message. The scripted world leaves out only fields the core does not read (`step_height_m`, `error_m`) or reads as true or false (`wheel_slip`, where `None` is false as `False` is). |
| 120, 122 | the limits `missed_heartbeats` and `link_max_age_s` take another value | The tests use the limits by name, so another value is not a bug they can see. The values are design choices, given in `decision.py` and the README. |
| 134, 146 | a type annotation (`X \| None` to `X & None`) | Python does not evaluate annotations inside a function. |
| 141, 149, 151, 170, 173, 177, 192, 213 | `= False` becomes `= None` | These flags are only tested for truth, and `None` is false. |
| 152, 153, 154, 155, 156, 157, 158, 159 | the first value of `down_seen_at`, `now` or `decision` | Each is set again before it is read: `now` and `decision` on every tick, `down_seen_at` whenever stairs down are seen, which a yes needs first. |
| 203 | tilt exactly `level_rad` counts as level | The limit is "about 3 degrees"; which side of it the value itself falls on has no meaning. |
| 210 | a yes lapses at exactly `down_forget_s` instead of just after | As for 203. |
| 278, 282 | a sequence of the behaviour tree keeps its memory | Every sequence finishes in the tick it starts, so there is nothing to remember. |
| 280, 281, 283, 287, 290, 295, 300, 303, 309, 312, 316, 324, 330, 335, 339, 343 | the name of a branch of the tree | The names are for people. The behaviour-tree page checks them against the page (#106). |

### `messages.py` (15)

| Mutants | What changes | Why the behaviour does not |
|---|---|---|
| 348, 349, 350, 351 | the values of `DECISION` and `DIRECTION` | They are only compared with each other. |
| 352 | `Message` is not frozen | As for the reports. |
| 356, 357 | the defaults `max_age_s` and `chars_per_s` | Design values, as for the limits. |
| 359 | the text of the error for `gap_s >= max_age_s` | Only the wording changes; the error is still raised. |
| 364, 372 | the first value of the queue's clock or of the last priority said | The first push sets the clock; the last priority is read only while a message is being said, so after one has gone out. |
| 393, 410, 439 | a repeat exactly `max_age_s` after the last one is no longer a repeat | As for 203. |
| 448 | a message that is not spoken takes 1 s instead of 0 s | It waits `gap_s` (1.5 s) either way, which is longer. |
| 494 | `continue` becomes `break` after stairs down the user said yes to | The confirmed step is always the last report in the list, so there is nothing after it. |

### `selector.py` and `link.py`

SELECTOR_LINK_SURVIVORS
