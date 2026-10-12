# Contributing to MANA

These rules apply to every member: Mushahid Hussain, Muhammad Waqas and Komail Raza.

## Branches

| Branch | Purpose | Who merges |
|---|---|---|
| `main` | The working branch. Releases are tagged from it (`v0.1.0-mid`, `v1.0.0-fyp1`) | Pull requests from feature branches |
| `feature/<module>-<task>` | One task from the tracker, for example `feature/m6-t9-decision-core` | The author opens a pull request into `main` |
| `fix/<short-name>` | A bug fix | Pull request into `main` |

Nobody pushes directly to `main`.

Module codes: `pm`, `ci`, `m1` (vision), `m2` (position), `m3` (connectivity), `m4` (voice and audio), `m5` (interface), `m6` (decision core), `sim`, `int`, `doc`, `rev`.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <what changed, in the present tense>
```

- Types: `feat`, `fix`, `test`, `docs`, `ci`, `refactor`, `chore`.
- The scope is the module code, for example `feat(m6): add stop-always-wins override`.
- Keep each commit to one logical change. Commit small and often. Do not save up a week of work for one push.

## Pull requests

1. Open the pull request into `main` and fill in the template.
2. Link the tracker task ID (for example `T9`) and the GitHub issue if there is one.
3. CI must be green before the pull request is merged.
4. At least one member who is **not the author** reviews and approves.
5. The author merges after approval, then deletes the branch.

## Every week

- Every member commits to their own module every week.
- On Friday we check commits and pull requests per member in the meeting log and fix any gap the next week.
