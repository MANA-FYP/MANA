# The FYP-1 poster

The poster of the department format (22.5 x 34.5 inches, portrait) for the mid evaluation and the
final presentation (#55).

| File | What it is |
|---|---|
| `MANA_poster.pdf` | the print file: one page, exactly 22.5 x 34.5 in |
| `MANA_poster_preview.jpg` | a small picture of it, for the README and the slides |
| `poster.html` | the poster itself: every icon is drawn inline, so it opens in any browser |
| `assets/` | the font (Montserrat), the FAST NUCES seal, the MANA emblem, the QR code |

## What is on it

The system figure (the five layers, and the three steps from the sensors to the command), the
results table, the live demo QR code, the iteration plan, the host devices, the tools, and the team
and supervisors in the department's order. Each number comes from a command, so it can be made again:

| On the poster | Where it comes from |
|---|---|
| Scripted scenarios, pass marks: MANA 71 of 71, baseline 60 of 71 | `python3 -m mana_core.evaluate --compare` |
| Wi-Fi drop, 200 runs with every mark met: MANA 186, baseline 0 | `python3 -m mana_core.repeat --runs 200 --seed 1`, the table in `docs/results.md` (#54) |
| Gazebo, 11 scenarios, mishaps: MANA 0, baseline 1 (it goes over the drop in `manual_drop`) | `python3 -m mana_core.evaluate --world gazebo --compare` (#81); the drive summary in `docs/results.md` once that is merged |

**The QR code** points to this repository, https://github.com/MANA-FYP/MANA. The live demo itself runs
on a laptop and a phone on the spot (`demo/README.md`), so it has no address of its own; the
repository has the guide to start it. If a page for the demo is made, change the code (below).

## Make it again

The poster is a web page at exactly 2250 x 3450 CSS pixels (100 px to the inch). Chrome makes both
files; run it in this folder, with the path of your Chrome:

```bash
# the picture (3375 x 5175 pixels, 150 dpi)
chrome --headless=new --hide-scrollbars --virtual-time-budget=5000 --force-device-scale-factor=1.5 \
  --window-size=2250,3450 --screenshot=MANA_poster.png poster.html
```

For the PDF, add this to the end of the `<style>` of a copy of `poster.html`, and print that copy
(`--print-to-pdf=MANA_poster.pdf --no-pdf-header-footer`). The page is 22.5 in wide, which is 2160
CSS pixels, so the whole page is scaled to 0.96:

```css
@page{size:22.5in 34.5in;margin:0}
html{zoom:.96;-webkit-print-color-adjust:exact;print-color-adjust:exact}
html,body{width:2250px;height:3448px;overflow:hidden}
```

To change the QR code: `pip install segno`, then
`python -c "import segno; segno.make('https://...', error='m').save('assets/qr_repo.png', scale=14, border=2, dark='#0A284A')"`.
Test it with a phone before it is printed.

## Before it is printed

- [ ] Each member checks the part for their own module (the issue asks for this):

| Module | Owner | On the poster | Checked |
|---|---|---|---|
| Decision core, baseline | Mushahid | the DECIDE tile, the rules, the scripted and Wi-Fi-drop rows | [ ] |
| Perception, voice | Waqas, Mushahid | the SENSE tile, the sensors | [ ] |
| Simulation, interface, CI | Komail | the ACT tile, the host devices, the Gazebo row, the tools | [ ] |

- [ ] The numbers still match `docs/results.md` on the day (run the three commands above).
- [ ] The iteration plan and its dates match the tracker.
- [ ] The QR code is scanned with two phones, from a printout.
- [ ] The names, roll numbers and titles are spelled as the department wants them (Dr., in roll-number order).
- [ ] A test print at A4 for the layout, then the real print.

## Licences of what is in it

- **Montserrat** (the font): SIL Open Font License 1.1.
- **Icons:** Material Symbols (Apache 2.0) and Simple Icons (CC0). The tools' logos are their owners' trademarks, shown to name the tool.
- **The FAST NUCES seal** is the university's logo, taken from its Wikipedia page, where it is marked as a non-free logo. Use it only as the university allows, and ask the department for the official file for the print (the brand guideline is on nu.edu.pk).
- **The MANA emblem** is ours.
