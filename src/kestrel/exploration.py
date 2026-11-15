"""The exploration experiment (CLAUDE.md, started 2026-11-16): one split of the coins,
fixed before any look, into an exploration half and a confirmation half.

- Exploration: anything goes. Runs are saved apart (``data/exploration/``), are not
  trials and are never evidence.
- Confirmation: locked like the holdout. The engine refuses a confirmation coin unless
  the run's config has been unlocked, which only a frozen confirmation note does
  (``kestrel backtest --confirmation``). Confirmation runs form their own trial pool.

Eligible coins are the stored universe's coins with at least ``MIN_MONTHS`` months of
daily bars before the holdout, delisted ones included, BTC, ETH and SOL left out (they
are already heavily searched). The split balances the halves by trading volume: coins
are ranked by their median daily quote volume before the holdout, taken in adjacent
pairs, and a seeded coin flip sends one of each pair to each half (an odd last coin goes
to exploration). Volume is a coin's size, not its returns: no outcome is looked at.

The split is written once to ``research/exploration/split.toml`` (committed) and never
rewritten; a second split would let the halves be chosen after a look.
"""

from __future__ import annotations

import random
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SPLIT_FILE = Path("research") / "exploration" / "split.toml"
SEED = 20261116
MIN_MONTHS = 24
SEARCHED = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT"})
CONFIRMATION_PREFIX = "confirmation-"
"""Research notes that may run on the confirmation half have slugs starting with this."""


class ExplorationError(ValueError):
    """The split is missing, malformed, or would be rewritten."""


class ConfirmationLockedError(Exception):
    """A run touched the confirmation half without a frozen confirmation note."""


@dataclass(frozen=True)
class Split:
    made: date
    seed: int
    exploration: frozenset[str]
    confirmation: frozenset[str]

    def side(self, symbol: str) -> str | None:
        s = symbol.upper()
        if s in self.exploration:
            return "exploration"
        if s in self.confirmation:
            return "confirmation"
        return None


def make_split(volumes: dict[str, float], seed: int = SEED) -> tuple[list[str], list[str]]:
    """(exploration, confirmation), each sorted, from each eligible coin's median volume."""
    ranked = sorted(volumes, key=lambda s: (-volumes[s], s))
    rng = random.Random(seed)
    exploration: list[str] = []
    confirmation: list[str] = []
    for i in range(0, len(ranked), 2):
        pair = ranked[i : i + 2]
        if len(pair) == 1:
            exploration.append(pair[0])
            continue
        first, second = pair if rng.random() < 0.5 else pair[::-1]
        exploration.append(first)
        confirmation.append(second)
    return sorted(exploration), sorted(confirmation)


def render(split: Split) -> str:
    def block(name: str, coins: frozenset[str]) -> str:
        lines = "\n".join(f'  "{c}",' for c in sorted(coins))
        return f"{name} = [\n{lines}\n]\n"

    return (
        "# The exploration experiment's split (CLAUDE.md). Fixed before any look; never\n"
        "# rewritten. Made by `kestrel exploration-split` from each coin's median daily\n"
        "# volume (no returns): adjacent pairs by volume, one of each pair to each half.\n"
        f"made = {split.made.isoformat()}\n"
        f"seed = {split.seed}\n"
        f"min_months = {MIN_MONTHS}\n\n"
        + block("exploration", split.exploration)
        + "\n"
        + block("confirmation", split.confirmation)
    )


def write_split(path: Path, split: Split) -> None:
    if path.exists():
        raise ExplorationError(f"{path} already exists; the split is never rewritten")
    if split.exploration & split.confirmation:
        raise ExplorationError("a coin can't be in both halves")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(split))


def load_split(path: Path) -> Split | None:
    """The split, or None if there isn't one (no experiment: nothing is locked)."""
    if not path.exists():
        return None
    try:
        raw = tomllib.loads(path.read_text())
        split = Split(
            made=raw["made"],
            seed=int(raw["seed"]),
            exploration=frozenset(s.upper() for s in raw["exploration"]),
            confirmation=frozenset(s.upper() for s in raw["confirmation"]),
        )
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as e:
        raise ExplorationError(f"{path} is malformed: {e}") from None
    if split.exploration & split.confirmation:
        raise ExplorationError(f"{path} puts a coin in both halves")
    return split


def check_symbols(symbols: list[str], split: Split | None, unlocked: bool, name: str) -> None:
    """Refuse any confirmation coin unless the config is unlocked."""
    if split is None or unlocked:
        return
    locked = sorted({s.upper() for s in symbols} & split.confirmation)
    if locked:
        shown = ", ".join(locked[:5]) + (f" and {len(locked) - 5} more" if len(locked) > 5 else "")
        raise ConfirmationLockedError(
            f"{name}: {shown} {'is' if len(locked) == 1 else 'are'} in the confirmation half, "
            "locked until a frozen confirmation note runs (kestrel backtest --confirmation)"
        )
