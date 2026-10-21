# Reproducible terminal demo for the README: `vhs docs/demo.tape` (brew install vhs).
# Records the audit, whatif and tune on whatever history is on this machine.
# The hidden prelude renames the maintainer's project directories to generic names;
# edit the sed to match yours or drop it.

Output docs/assets/demo.gif

Set Shell bash
Set FontSize 13
Set Width 1360
Set Height 960
Set Padding 16
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

Hide
Type "source docs/demo-prelude.sh; clear" Enter
Show

Type "kvleak" Enter
Sleep 9s

Type "clear" Enter
Type "kvleak whatif" Enter
Sleep 14s

Type "clear" Enter
Type "kvleak tune" Enter
Sleep 13s
