# Pilot test: people outside the team drive MANA

Five to eight people who are not on the team drive the test drive in Gazebo, and we learn whether
what MANA did and what it told them made sense to them (#90). This is a pilot of a simulation, not
a study of people who use assistive devices, and nothing here is a medical claim. It is the last
of the real-world readiness checks (#82 to #90): it shows where MANA's actions and messages are
unclear, so the report can state its limits.

The driving and recording side is #81; the participant side, the tasks, the questionnaire and the
consent form are here. The results go in `docs/results.md` and the report.

## Before the first session

- [ ] Ask the department what it requires for a test with people (consent, anonymity, ethics
      review). The consent form below is written for that: fill in the contact line, and change it
      if the department asks for something else. **No session until this is answered.**
- [ ] Ask the supervisors if they will take part (optional).
- [ ] Print the consent form (one for each person, one to keep) and the questionnaire.
- [ ] Do the whole session once with a team member, with a stopwatch, and fix what does not work.

## Who takes part

- 5 to 8 people who have not seen MANA drive: classmates, friends, and the supervisors if they
  agree. Not the team, and not anyone who has read the report.
- Adults who give consent. Nobody is asked about a disability or a diagnosis, and nobody is
  recruited for one.
- Names are never written down with the results. Each person gets a code (P01, P02, ...) and
  the list that links codes to names stays on paper with the facilitator and is thrown away
  once the report is handed in.

## Roles in a session

| Role | Does | Who |
|---|---|---|
| Participant | drives and says what they think is happening | an outside person |
| Facilitator | reads the words, gives the tasks, answers only "what do I do next" | Komail |
| Operator | starts the simulation, switches the faults, takes the rosbag (#81) | Mushahid |
| Recorder | fills in the log sheet, writes what the participant says | a third team member |

## Setting up a session (about 10 minutes)

Needs Ubuntu 24.04 with ROS 2 Jazzy and Gazebo Harmonic, as in `src/mana_sim/README.md`.

```bash
source /opt/ros/jazzy/setup.bash && source install/setup.bash
python3 -m mana_sim.campus --start before_steps > /tmp/campus.sdf   # the campus (#79), at a start of the task
ros2 launch mana_sim world.launch.py world:=/tmp/campus.sdf driver:=true panel:=true
ros2 run mana_sim keyboard --ros-args -p use_sim_time:=true          # the participant's keys
```

The start places are listed in the campus section of `src/mana_sim/README.md`; change `--start` for
each task.

- The participant sits at the laptop with the Gazebo window and the keyboard. The facilitator keeps
  the supervisor's panel (http://localhost:8090) on a second screen: it shows the decision, the
  fault buttons and the message MANA says to the user.
- The participant must be told what MANA says. Where the panel would give the answer away (the
  reports and the reasons), keep it on the facilitator's screen and read the message line to the
  participant, or turn the screen. A participant view with only the Gazebo window and the message
  line is not built; if it is wanted it is a task for the panel.
- Start the recording of the drive (#81) before the participant starts, and say its name out
  loud so the log sheet and the recording belong together. Until that recorder is merged, the log
  sheet (and a screen recording, if the participant agreed) is the record.
- Keys, for the participant (a card on the table): arrows to drive (the first arrow takes the
  controls), `h` to give the controls back to the autopilot, `x` to stand still, space to STOP,
  `g` to GO, `y` and `n` to answer MANA.

## What to say first (read it aloud)

> We are testing MANA, a program that helps a wheelchair decide how to move. We are testing the
> program, not you: nothing you do is wrong. You can stop at any time without giving a reason.
> Please say aloud what you think is happening and why, and what you read or hear. I will not
> answer questions about what MANA is doing until we have finished; I will tell you what to do
> next. Nothing is recorded unless you agreed on the form.

## The tasks

A warm-up first: two minutes driving on level ground with the arrows, so the keys are known.
Each task has a start place from the campus (`src/mana_sim/README.md`) and ends when the
participant says they are done, reaches the marked place, or after five minutes.

| # | Task (said to the participant) | Start | The operator | Note down |
|---|---|---|---|---|
| 1 | "The chair can drive itself to a place. Watch it go, then take over when you want to and drive to the place I show you." | the plaza | sends the autopilot to a goal (#78) | when they took over and why; what they thought it was doing |
| 2 | "Drive up to the building and go in through the south door." | before the steps | nothing | what they thought when it changed to tracks, or stopped; whether they understood why |
| 3 | "Cross the hall to the far wall." | the hall, tables in the way | nothing | any stop they did not expect; what MANA said and what they thought it meant |
| 4 | "Go from the corridor to the dark room, then out again." | the corridor | switches on the dark-room faults, a lost GPS, then a lost Wi-Fi, one at a time, the participant not told | what they noticed; what they thought the messages meant; whether they trusted it more or less |

If a task cannot be done (the chair is stuck, the simulation fails), write why, and carry on with
the next one. A run that failed is a result.

## What the recorder writes down

One row for each thing that happens, in `docs/pilot/session-log-template.csv`:

| Column | Meaning |
|---|---|
| participant | P01, P02, ... |
| task | 1 to 4, or warm-up |
| time_s | seconds into the task |
| event | takeover, handback, unexpected stop, misunderstood message, fault noticed, other |
| what_mana_did | its decision and the message, as the panel shows them |
| what_they_said | their words, as close as you can write them |
| what_they_understood | in the participant's words, or "not asked" |

Three counts are made from it for each person and for the whole group: the takeovers, the stops
the participant did not expect, and the messages they misunderstood (what they said it meant is not
what it was meant to say).

After each task, ask these three questions, in this order, and write the answers:

1. What did you think the chair was doing just now?
2. Was there a moment you wanted to stop it or take over? When?
3. Did anything MANA said or did surprise you?

## The questionnaire

After the four tasks, on paper, before any talk about how it went. Each statement is answered from
1 (strongly disagree) to 5 (strongly agree). These are the ten statements of the System Usability
Scale (Brooke, 1996), word for word.

1. I think that I would like to use this system frequently.
2. I found the system unnecessarily complex.
3. I thought the system was easy to use.
4. I think that I would need the support of a technical person to be able to use this system.
5. I found the various functions in this system were well integrated.
6. I thought there was too much inconsistency in this system.
7. I would imagine that most people would learn to use this system very quickly.
8. I found the system very cumbersome to use.
9. I felt very confident using the system.
10. I needed to learn a lot of things before I could get going with this system.

Three questions to answer in words:

- When MANA stopped or slowed down, did you understand why?
- Which message, if any, did you not understand?
- What would you change first?

Type the numbers into `docs/pilot/sus-responses-template.csv` (a copy for the real answers, kept
out of git until the names are gone), then:

```bash
python3 tools/sus_score.py answers.csv
```

It scores each person (0 to 100), and prints the mean, the median, the spread and the mean answer
of each statement. A score is the SUS only for the whole scale; the items are not scores of their
own. The usual average, 68, is a point of comparison, not a target we set.

## Consent form (one page, for each participant)

> **MANA pilot test: information and consent**
>
> *What this is.* A student project (FAST NUCES, Islamabad Campus, final year project F26-049)
> that makes a program, MANA, that helps a wheelchair decide how to move. You will drive a
> simulated chair on a computer for about 30 minutes, and then answer ten statements and three
> questions. You are testing the program, not yourself.
>
> *What we record.* What the program did and what you pressed, your answers, and notes of what you
> said. We do **not** record your name with them: you get a code. We record your voice or the screen
> only if you tick the box below, and only to check the notes; recordings are deleted when the
> report is handed in.
>
> *Your choices.* Taking part is voluntary. You can stop at any moment, and you can ask for what
> you gave to be deleted until the report is handed in, without giving a reason. It will not affect
> your marks or anything else. Nobody who is on the team is told your code.
>
> *What we do with it.* The results are used in the project report as counts, an average and short
> quotes without names. This is a test of a simulation, not a medical or safety test, and says
> nothing about your abilities.
>
> *Questions or a complaint:* [name and contact of the supervisor or the department, to be filled
> in before the first session].
>
> - [ ] I have read this and I agree to take part.
> - [ ] I agree that my voice and the screen are recorded to check the notes.
>
> Name: ____________________ Date: ____________ Signature: ____________________
> Code given (written by the facilitator): P__

## Reporting

Put this in `docs/results.md` and the report when the sessions are done. Say how many people, how
they were found, and that they are not users of assistive devices; the limits come with the numbers.

| | Number |
|---|---|
| Participants | |
| Takeovers (all, and per person) | |
| Unexpected stops (all, and per person) | |
| Messages misunderstood (all, and which) | |
| SUS, mean and spread | |
| Tasks not completed, and why | |

**Limits to state:** a small group, mostly students, a simulation without the real chair, a
keyboard instead of a joystick, and a SUS mean that is only a rough guide below ten people.

## Each problem found becomes an issue

Open one for each problem, with the label `INT integration`, and say: the participant code and
task, what MANA did and said, what the participant understood, and which of the ten scenarios, if
any, it should have been one of. Put the issue number next to it in the report.
