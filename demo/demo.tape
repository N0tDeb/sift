# Sift CLI demonstration for the project README.
#
# Generate on GitHub: Actions > Generate Sift VHS Demo > Run workflow.
# Or, with VHS and Sift installed locally, run: vhs demo/demo.tape
# Output: demo/sift.gif
#
# Three operations on demo/orders.csv:
#   check:  seven findings, including ambiguous dates.
#   impact: direct coercion produces -1,998; supported repairs give 10,149.75.
#   fix:   13 cell changes; the ambiguous dates are left for review.
#
# Keep commands and figures synchronized with the current CLI and demo README.

Output demo/sift.gif

Set Shell "bash"
Set FontSize 15
Set Width 1100
Set Height 860
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms
Set PlaybackSpeed 1.0

Hide
Type "cd demo && clear" Enter
Show

Sleep 900ms

# Check the file and display findings.
Type "sift-lint check orders.csv --key order_id"
Sleep 600ms
Enter
Sleep 5s

Type "clear" Enter
Sleep 400ms

# Compare raw and repaired aggregates.
Type "sift-lint impact orders.csv --sum total --group-by region"
Sleep 600ms
Enter
Sleep 6s

Type "clear" Enter
Sleep 400ms

# Apply supported repairs and leave ambiguous dates unchanged.
Type "sift-lint fix orders.csv --min-confidence medium --output clean.csv"
Sleep 600ms
Enter
Sleep 6s

Sleep 1500ms
