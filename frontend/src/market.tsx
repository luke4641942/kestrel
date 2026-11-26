import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { MARKETS, marketNotice, type Market } from "./markets";

const KEY = "kestrel.market";

function stored(): Market {
  try {
    const v = localStorage.getItem(KEY);
    return MARKETS.some((m) => m.id === v) ? (v as Market) : "all";
  } catch {
    return "all";
  }
}

const MarketContext = createContext<{ market: Market; setMarket: (m: Market) => void }>({
  market: "all",
  setMarket: () => {},
});

/** The market every page shows, remembered in this browser (a convenience; "All" if not). */
export function MarketProvider({ children }: { children: ReactNode }) {
  const [market, setMarket] = useState<Market>(stored);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, market);
    } catch {
      /* storage unavailable: the choice lasts for this session */
    }
  }, [market]);
  return <MarketContext.Provider value={{ market, setMarket }}>{children}</MarketContext.Provider>;
}

export const useMarket = () => useContext(MarketContext);

/** The top bar's market picker: All, Crypto, Forex, Futures (parked, listed but marked). */
export function MarketSwitch() {
  const { market, setMarket } = useMarket();
  const parked = MARKETS.find((m) => m.id === market)?.parked;
  return (
    <label className="market-pick" title={parked ?? "Which market every page shows"}>
      <span className="market-pick-label">Market</span>
      <select className="market-select" aria-label="Market" value={market} onChange={(e) => setMarket(e.target.value as Market)}>
        {MARKETS.map((m) => (
          <option key={m.id} value={m.id}>
            {m.parked ? `${m.label} (parked)` : m.label}
          </option>
        ))}
      </select>
      <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden>
        <path d="m2 3.5 3 3 3-3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </label>
  );
}

/** Above a page built for crypto only: says so under another market. */
export function MarketNotice({ route }: { route: string }) {
  const { market } = useMarket();
  const notice = marketNotice(route, market);
  return notice ? (
    <div className="market-notice" role="note">
      {notice.text} {notice.showingCrypto && <span className="secondary">Showing crypto.</span>}
    </div>
  ) : null;
}

/** A small label naming an item's market, shown when every market is on screen. */
export function MarketBadge({ cls }: { cls: string }) {
  const { market } = useMarket();
  if (market !== "all") return null;
  const label = cls === "forex" ? "Forex" : cls === "futures" ? "Futures" : "Crypto";
  return <span className={`chip market-${cls}`}>{label}</span>;
}
