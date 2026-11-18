# Strategy backlog

Ideas not yet scheduled, and where each stands. What has been tested, and what it found,
is in `findings.md`; decisions are in `decisions.toml`; the active work is in `TODO.md`.
Results recorded before Kestrel (Nov 2024 - Oct 2026) don't count: they came from scripts
with synthetic data, in-sample fitting, lookahead, sweeps on the test data, or missing
costs. Each idea is rebuilt as a pre-registered note and tested on real Binance data.

Status as of 2026-11-16.

## Candidates

| Idea | What it needs | Spot-viable? |
|---|---|---|
| Altcoin portfolio on the BTC regime rule | A rule for which 3 coins to hold inside the regime (findings 14: pickers alone lose with the market), the delisting exit rule, an unspent holdout (any coin but BTC, ETH and SOL). Next in TODO.md | Yes |
| Delisting exit rule | A note: sell a held coin at the next open after its delisting is announced (findings 11). Belongs with the altcoin portfolio | Yes, as a safety rule |
| News tone (GDELT) | Code built; the news history to download, then a note | Yes |
| Funding momentum | Findings 14 found crowded (high-funding) coins did better than uncrowded ones, the opposite of the idea tested. A new note with that result disclosed; as money the crowded fifth still lost 53% alone, so only as a picker inside a regime rule | Signal yes |
| Forward market-structure data | Binance's live liquidation and order-book streams recorded from now on, untouched by construction; needs an always-on machine (parked) | Data first |
| Token unlocks | Unlock schedules: free in DefiLlama's dataset files (2026-11-18; schedules as documented today). Measure an unlock against the coin's normal daily volume, not only its circulating supply: the market's capacity to absorb it is the mechanism (outside review, 2026-11-16) | Yes, as an exit rule |
| BTC's regime turning volatile before ETH's | Only 11 such moments on daily regimes 2021-25: too few. Revisit with hourly regimes for more events | Yes |
| HMM regime filter as a strategy | A note that passes the entry bar; a filter on trend-vol-target already failed (findings 8) | Yes, as a filter |

## Out of reach for this account

Shorting ideas are studied hypothetically (TODO.md, "Shorting research"); a result that
would be pursued is recorded as needs-shorting, not traded.

| Idea | Why |
|---|---|
| Funding carry, perp/spot basis, delisting shorts | Need shorting; Binance perps aren't offered to Australian retail (P5 parked). The delisting short was also mostly not shortable on Binance (findings 11) |
| Options volatility premium | Needs selling options |
| Perp positioning: funding extremes, open-interest divergence (outside review, 2026-11-16) | As trades they need perps. As a long-only spot signal, funding was already tested as a picker and lost (findings 14). Open-interest history isn't stored (Binance's API serves about a month; its bulk metrics files may go back further, unchecked) |
| Liquidation, order-book and lead-lag trading at minute horizons | Costs at 330 USDT rule out minute horizons (the cost gate and three failed 1-5 minute strategies) |

## Rules every test follows

- Spot, long only, costs always included (7.5 bps fee with the BNB discount, each coin's
  half-spread band, square-root impact); decide at a bar's close, fill at the next open.
- The note is frozen before the first run; every run is a counted trial; a setting chosen
  after seeing results is a new trial.
- The full scorecard (CLAUDE.md); a study comes before a strategy when the effect itself
  is in doubt.
- The holdout (from 2025-11-01) is used once per strategy, for a finished strategy's final
  verdict; it is spent for BTC, ETH and SOL.
