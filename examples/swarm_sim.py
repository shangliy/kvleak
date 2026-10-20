"""Back-of-the-envelope model of a multi-agent swarm's input-token bill.

Compares a "naive" swarm with one that follows docs/playbook.md:

  naive     : full logs pasted on the shared board, the fixer is respawned every
              round, and the reviewer runs on a different model than the author
              (so its context is rebuilt every hand-off)
  playbook  : pointers on the board, warm long-lived workers, one model per worker

Every worker re-reads its whole context each round (cache read), and pays the
write price for whatever is new. The numbers are illustrative, not measured;
change the constants and re-run.

    python examples/swarm_sim.py
"""

from dataclasses import dataclass

WORKERS = 6
ROUNDS = 40
BASE = 30_000  # system prompt + tool schemas per worker
OWN_WORK = 2_000  # new tokens each worker adds per round
LOG = 8_000  # one failing build log per round (~500 lines)
POINTER = 80  # {path, sha256, lines, status} instead of the log
READ, WRITE = 0.05, 1.25  # Claude Opus 5.5, 5-minute TTL
USD_PER_MTOK = 4.00


@dataclass
class Bill:
    tokens: float = 0.0

    def call(self, cached: int, new: int) -> None:
        self.tokens += cached * READ + new * WRITE

    @property
    def usd(self) -> float:
        return self.tokens * USD_PER_MTOK / 1e6


def simulate(paste_logs: bool, respawn_fixer: bool, cross_model_review: bool) -> Bill:
    bill = Bill()
    ctx = [0] * WORKERS
    for w in range(WORKERS):  # every worker starts cold once
        bill.call(0, BASE)
        ctx[w] = BASE
    post = LOG if paste_logs else POINTER
    for _ in range(ROUNDS):
        for w in range(WORKERS):
            new = OWN_WORK + post
            if w == 0:  # the fixer
                if not paste_logs:
                    new += LOG  # only the fixer opens the log file
                if respawn_fixer:  # fresh agent: base prompt + the context it needs, all cold
                    bill.call(0, BASE + new)
                    continue
            if w == 1 and cross_model_review:  # reviewer on another model: no shared cache
                bill.call(0, ctx[w] + new)
            else:
                bill.call(ctx[w], new)
            ctx[w] += new
    return bill


def main() -> None:
    scenarios = [
        ("naive (paste logs, respawn, switch models)", simulate(True, True, True)),
        ("+ pointers on the board", simulate(False, True, True)),
        ("+ warm fixer", simulate(False, False, True)),
        ("playbook (+ one model per worker)", simulate(False, False, False)),
    ]
    base = scenarios[0][1].tokens
    print(f"{WORKERS} workers x {ROUNDS} rounds, Opus 5.5 prices\n")
    print(f"{'scenario':46s} {'input tok-equiv':>16s} {'USD':>9s} {'vs naive':>9s}")
    for name, bill in scenarios:
        print(f"{name:46s} {bill.tokens / 1e6:15.1f}M {bill.usd:9.2f} {bill.tokens / base - 1:+9.0%}")


if __name__ == "__main__":
    main()
