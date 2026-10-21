# Sourced by docs/demo.tape before recording. Renames the maintainer's project
# directories to generic names of the same length (columns stay aligned);
# edit to match yours or delete the function.
export PATH="$HOME/anaconda3/bin:$HOME/.local/bin:$PATH"
export FORCE_COLOR=1
kvleak() {
  command kvleak "$@" | sed -E '
    s/JEVRecom/recsys-a/g
    s/recsys-evolve-kit/evolve-kit-recsys/g
    s/agent-newsroom/newsroom-agent/g
    s/package-drfat-2026-10-12/paper-draft-2026-10-12  /g
    s/substrack/tracker-a/g
    s/baylearn2026/talk-2026-ml/g
    s/researchIdea/ideation-log/g
    s#/home/[^/ ]+/\.claude#~/.claude#g'
}
