import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Bars } from "../api";

const UP = "#3fb68b";
const DOWN = "#e5534b";

interface Handles {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  volume: ISeriesApi<"Histogram">;
}

/**
 * Candles with a volume histogram underneath.
 * The chart and its series share one lifetime (created and destroyed together);
 * new bars only replace series data, which avoids tearing down the chart on every refresh.
 */
export function CandleChart({ bars }: { bars: Bars }) {
  const el = useRef<HTMLDivElement>(null);
  const handles = useRef<Handles>();

  useEffect(() => {
    if (!el.current) return;
    const chart = createChart(el.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#8a92a0",
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "rgba(37,42,51,0.6)" },
        horzLines: { color: "rgba(37,42,51,0.6)" },
      },
      rightPriceScale: { borderColor: "#252a33" },
      timeScale: { borderColor: "#252a33", timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.22 } });
    handles.current = { chart, candles, volume };

    return () => {
      handles.current = undefined;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const h = handles.current;
    if (!h) return;
    h.candles.setData(
      bars.time.map((t, i) => ({
        time: t as UTCTimestamp,
        open: bars.open[i]!,
        high: bars.high[i]!,
        low: bars.low[i]!,
        close: bars.close[i]!,
      })),
    );
    h.volume.setData(
      bars.time.map((t, i) => ({
        time: t as UTCTimestamp,
        value: bars.volume[i]!,
        color: bars.close[i]! >= bars.open[i]! ? "rgba(63,182,139,0.35)" : "rgba(229,83,75,0.35)",
      })),
    );
    h.chart.timeScale().fitContent();
  }, [bars]);

  return <div ref={el} className="chart" />;
}
