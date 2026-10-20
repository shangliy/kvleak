"""Prompt-cache price model.

Everything in kvleak is computed in *base-equivalent tokens* (1 token billed at
the model's normal uncached input price) and only converted to dollars at the
edge. That keeps the math provider-agnostic: if a model has no known dollar
price, every report still works in tokens.

Multipliers are relative to the base input price:

  uncached input        1.00x
  cache write, 5m TTL   1.25x
  cache write, 1h TTL   2.00x
  cache read            ~0.10x (model dependent; 0.05x / 0.025x on newer tiers)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

WRITE_5M = 1.25
WRITE_1H = 2.00
DEFAULT_READ = 0.10
TTL_5M = 300
TTL_1H = 3600


@dataclass(frozen=True)
class Price:
    name: str
    input_per_mtok: Optional[float]  # USD per 1M uncached input tokens; None = unknown
    read_mult: float = DEFAULT_READ
    write_5m_mult: float = WRITE_5M
    write_1h_mult: float = WRITE_1H

    def usd(self, base_equiv_tokens: float) -> Optional[float]:
        if self.input_per_mtok is None:
            return None
        return base_equiv_tokens * self.input_per_mtok / 1_000_000

    def keepalive_kmax(self, ttl_seconds: int = TTL_5M) -> int:
        """Max keep-alive pings that are still cheaper than letting the cache die.

        Letting a prefix of N tokens expire costs an extra (write - read) * N on the
        next turn; each keep-alive ping costs read * N. So pinging pays off for up
        to floor((write - read) / read) consecutive pings.
        """
        write = self.write_1h_mult if ttl_seconds >= TTL_1H else self.write_5m_mult
        return int((write - self.read_mult) / self.read_mult + 1e-9)


# Longest matching key wins, so "claude-opus-5-5" beats "claude-opus".
# Sources: Anthropic public pricing (Oct 2026). PRs welcome for other providers.
BUILTIN: Dict[str, Price] = {
    p.name: p
    for p in [
        Price("claude-fable-5-1", 10.00, read_mult=0.025),
        Price("claude-mythos-5-1", 10.00, read_mult=0.025),
        Price("claude-fable-5", 10.00),
        Price("claude-opus-5-5", 4.00, read_mult=0.05),
        Price("claude-opus-5", 5.00),
        Price("claude-opus-4", 5.00),
        Price("claude-opus", 5.00),
        Price("claude-sonnet-5", 2.00),
        Price("claude-sonnet-4", 3.00),
        Price("claude-sonnet", 3.00),
        Price("claude-haiku-5-5", 0.10),
        Price("claude-haiku-4-5", 1.00),
        Price("claude-haiku", 1.00),
    ]
}

UNKNOWN = Price("unknown", None)


def _load_overrides() -> Dict[str, Price]:
    """User pricing file: $KVLEAK_PRICING or ~/.config/kvleak/pricing.json.

    Format: {"gemini-2.5-pro": {"input_per_mtok": 1.25, "read_mult": 0.25}, ...}
    """
    path = os.environ.get("KVLEAK_PRICING") or str(Path.home() / ".config/kvleak/pricing.json")
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for name, cfg in raw.items():
        if isinstance(cfg, dict):
            out[name] = Price(
                name,
                cfg.get("input_per_mtok"),
                read_mult=cfg.get("read_mult", DEFAULT_READ),
                write_5m_mult=cfg.get("write_5m_mult", WRITE_5M),
                write_1h_mult=cfg.get("write_1h_mult", WRITE_1H),
            )
    return out


_TABLE: Optional[Dict[str, Price]] = None


def table() -> Dict[str, Price]:
    global _TABLE
    if _TABLE is None:
        _TABLE = {**BUILTIN, **_load_overrides()}
    return _TABLE


def price_for(model: Optional[str]) -> Price:
    if not model:
        return UNKNOWN
    m = model.lower()
    best = None
    for key, price in table().items():
        if key in m and (best is None or len(key) > len(best.name)):
            best = price
    return best or UNKNOWN
