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

/** The top bar's switcher: All · Crypto · Forex · Futures (parked, shown but marked). */
export function MarketSwitch() {
  const { market, setMarket } = useMarket();
  return (
    <div className="seg market-switch" role="group" aria-label="Market">
      {MARKETS.map((m) => (
        <button
          key={m.id}
          type="button"
          aria-pressed={market === m.id}
          className={m.parked ? "parked" : undefined}
          title={m.parked}
          onClick={() => setMarket(m.id)}
        >
          {m.label}
        </button>
      ))}
    </div>
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
