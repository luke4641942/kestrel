"""Which market a strategy, note, decision or run belongs to (Luke, 2026-11-23).

Kestrel researches more than one market, each with its own data, engine, holdout and
trial pool: crypto (Binance spot), forex (P6) and, parked, futures. The market is read
from the implementation's key, so no file has to say it twice: ``fx:`` keys trade forex,
everything else (strategies and studies) trades crypto. Binance's EURUSDT and PAXGUSDT
are crypto here: they were traded on Binance, through the crypto engine.
"""

from __future__ import annotations

from typing import Literal

AssetClass = Literal["crypto", "forex", "futures"]
ASSET_CLASSES: tuple[AssetClass, ...] = ("crypto", "forex", "futures")
POOL_OF_CLASS: dict[AssetClass, str] = {"crypto": "main", "forex": "forex", "futures": "futures"}
"""The trial pool each market's results are judged against (kestrel.validation.deflated)."""


def asset_class(implementation: str | None) -> AssetClass:
    """The market an implementation trades; a draft with none yet is crypto's default."""
    if implementation and implementation.startswith("fx:"):
        return "forex"
    return "crypto"
