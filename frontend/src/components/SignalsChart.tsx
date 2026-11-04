import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Signals } from "../api";
import { cssVar, useTheme } from "../theme";

interface Handles {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  weight: ISeriesApi<"Histogram">;
  markers: ISeriesMarkersPluginApi<Time>;
}

/**
 * Candles with the strategy's target-weight changes marked, and the weight in a pane
 * underneath. A weight decided at bar t's close fills at bar t+1's open, so markers sit
 * on bar t+1, where the engine would trade (before the no-trade band is applied).
 */
export function SignalsChart({ signals }: { signals: Signals }) {
  const el = useRef<HTMLDivElement>(null);
  const handles = useRef<Handles>();
  const { theme } = useTheme();

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, sans-serif",
        fontSize: 11,
        attributionLogo: false,
      },
      // See EquityChart: let years of 4h bars fit instead of showing only the latest.
      timeScale: { timeVisible: true, secondsVisible: false, minBarSpacing: 0.001 },
      crosshair: { mode: 0 },
    });
    // Formats are per series so each pane's axis gets its own (prices on top, % below);
    // a chart-wide priceFormatter would override the weight pane's percentages.
    const candles = chart.addSeries(CandlestickSeries, {
      borderVisible: false,
      priceLineVisible: false,
      priceFormat: {
        type: "custom",
        formatter: (p: number) => p.toLocaleString("en-AU", { maximumFractionDigits: 2 }),
        minMove: 0.01,
      },
    });
    const weight = chart.addSeries(
      HistogramSeries,
      {
        priceLineVisible: false,
        lastValueVisible: false,
        priceFormat: { type: "custom", formatter: (p: number) => `${Math.round(p * 100)}%`, minMove: 0.01 },
      },
      1,
    );
    chart.panes()[1]?.setHeight(90);
    handles.current = { chart, candles, weight, markers: createSeriesMarkers(candles, []) };
    return () => {
      handles.current = undefined;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const h = handles.current;
    if (!h) return;
    const up = cssVar("--up");
    const down = cssVar("--down");
    const line = cssVar("--line");
    const muted = cssVar("--text-3");
    h.chart.applyOptions({
      layout: { textColor: muted, panes: { separatorColor: line } },
      grid: { vertLines: { visible: false }, horzLines: { color: line } },
      rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line },
    });
    h.candles.applyOptions({ upColor: up, downColor: down, wickUpColor: up, wickDownColor: down });
    h.weight.applyOptions({ color: cssVar("--text-2") });

    const t = (i: number) => signals.time[i] as UTCTimestamp;
    h.candles.setData(
      signals.time.map((_, i) => ({
        time: t(i),
        open: signals.open[i]!,
        high: signals.high[i]!,
        low: signals.low[i]!,
        close: signals.close[i]!,
      })),
    );
    h.weight.setData(signals.time.map((_, i) => ({ time: t(i), value: signals.weight[i]! })));

    const markers: SeriesMarker<Time>[] = [];
    if (signals.kind === "entries") {
      // Bracket strategies: mark each entry signal on the bar it would fill (the next open).
      for (let i = 0; i + 1 < signals.time.length; i++) {
        if (signals.weight[i]) {
          markers.push({ time: t(i + 1), position: "belowBar", shape: "arrowUp", color: up });
        }
      }
      h.markers.setMarkers(markers);
      h.chart.timeScale().fitContent();
      return;
    }
    for (let i = 1; i + 1 < signals.time.length; i++) {
      const prev = signals.weight[i - 1]!;
      const next = signals.weight[i]!;
      if (next === prev) continue;
      const buying = next > prev;
      markers.push({
        time: t(i + 1),
        position: buying ? "belowBar" : "aboveBar",
        shape: buying ? "arrowUp" : "arrowDown",
        color: buying ? up : down,
        // Label only partial sizes; all-in and all-out are clear from the arrow.
        text: next > 0 && next < 1 ? `${Math.round(next * 100)}%` : undefined,
      });
    }
    h.markers.setMarkers(markers);
    h.chart.timeScale().fitContent();
  }, [signals, theme]);

  return <div ref={el} className="chart signals-chart" />;
}
