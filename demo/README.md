# Recording the demo

The GIF at the top of the README is generated, not hand-captured, so it can be
re-recorded whenever the output changes.

## With VHS (recommended — produces the GIF)

[VHS](https://github.com/charmbracelet/vhs) reads a script and renders a
terminal session to a GIF. Nothing is captured live, so a typo in the take
does not mean starting over.

```bash
brew install vhs          # or: go install github.com/charmbracelet/vhs@latest
pip install -e .          # from the repo root, so `sift` is on PATH
vhs demo/demo.tape        # writes demo/sift.gif
```

Then reference it as the first thing in the README, above the prose:

```markdown
![Sift finding problems in a CSV](demo/sift.gif)
```

A GIF is heavier than a link, so keep it under about 3 MB. If it comes out
larger, drop `Set FontSize` to 13 and `Set Width` to 1000 rather than cutting a
beat — the third beat, where Sift refuses to guess, is the one worth keeping.

## With asciinema (lighter, but a link rather than an inline image)

asciinema records real keystrokes and uploads a playable, copy-pasteable
recording. Text rather than pixels, so it stays sharp and weighs nothing — but
it cannot be embedded directly in a README the way a GIF can.

```bash
pip install asciinema
cd demo
asciinema rec sift.cast --cols 100 --rows 42 --idle-time-limit 1.5
```

Then run the same three commands, pausing a beat after each:

```bash
sift check orders.csv --key order_id
clear
sift impact orders.csv --sum total --group-by region
clear
sift fix orders.csv --min-confidence medium --output clean.csv
exit
```

`--idle-time-limit 1.5` trims your thinking time out of the playback, so you
can take as long as you like between commands. Upload with `asciinema upload
sift.cast` and put the badge it gives you near the top of the README.

## Why these three commands

The demo file is small on purpose — ten rows, five columns — because the point
is not that Sift finds a lot, it is that what it finds would otherwise reach
production silently.

1. **`check`** shows the breadth in one screen: dates that read two ways, a
   region spelled three ways, prices that are text, and `-999` sitting in a
   money column.
2. **`impact`** is the beat that lands. `SUM(total)` over this file returns
   **-1,998** — the only two values `float()` can read are the placeholders.
   Every real price is `$1,299.00` and gets dropped. No exception is raised.
3. **`fix`** repairs thirteen cells and then refuses the date column, because
   nothing in the file says whether `04/03/2024` is March or April. The
   refusal is the most important frame in the recording: it is the difference
   between a cleaner and a tool you can trust with your data.

## Keeping it honest

Re-record after any change to output formatting. A GIF showing output the tool
no longer produces is worse than no GIF, and it is exactly the kind of drift
the tests in this repo exist to prevent everywhere else.
