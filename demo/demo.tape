# Sift — README demo
#
# Records the GIF at the top of the README. Install the recorder first:
#
#     brew install vhs          # or: go install github.com/charmbracelet/vhs@latest
#     pip install -e .          # from the repo root, so `sift` is on PATH
#     vhs demo/demo.tape        # writes demo/sift.gif
#
# The recording has three beats and no narration, because the output argues
# better than a caption would:
#
#   1. check   — the breadth. Seven findings in one screen, an error at the top.
#   2. impact  — the money shot. This file's SUM returns MINUS 1,998.
#   3. fix     — the judgement. Thirteen cells repaired, one thing refused.
#
# Timing is deliberate. The pauses after each command are long enough to read
# the first two lines of output and no longer; anyone who wants the detail can
# read the README. Total runtime is about 25 seconds.

Output demo/sift.gif

Set Shell "bash"
Set FontSize 15
Set Width 1100
Set Height 860
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms
Set PlaybackSpeed 1.0

# Start on a clean prompt with no repo clutter in view.
Hide
Type "cd demo && clear" Enter
Show

Sleep 900ms

# --- Beat 1: what is wrong with this file -----------------------------------
Type "sift check orders.csv --key order_id"
Sleep 600ms
Enter
Sleep 5s

Type "clear" Enter
Sleep 400ms

# --- Beat 2: how wrong is my number -----------------------------------------
# The line that does the work: "coerced  -1,998" against "after sift fix
# 10,149.75". A negative total from a file of positive prices.
Type "sift impact orders.csv --sum total --group-by region"
Sleep 600ms
Enter
Sleep 6s

Type "clear" Enter
Sleep 400ms

# --- Beat 3: fix what can be derived, refuse the rest -----------------------
# The refusal is the point. The dates are genuinely undecidable, so Sift
# hands them back rather than guessing.
Type "sift fix orders.csv --min-confidence medium --output clean.csv"
Sleep 600ms
Enter
Sleep 6s

Sleep 1500ms
