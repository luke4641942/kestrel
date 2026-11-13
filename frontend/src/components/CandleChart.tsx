import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, type CSSProperties } from "react";
import type { Bars, IndicatorInfo } from "../api";
import { INDICATOR_PANE_PX, indicatorPlan, ownPanes } from "../indicators";
import { IndicatorLayer } from "./indicatorLayer";
import { cssVar, useTheme } from "../theme";

interface Handles {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  volume: ISeriesApi<"Histogram">;
}

function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/**
 * Candles with a volume histogram underneath, coloured from the active theme's tokens.
 * The chart and its series share one lifetime; new bars or a theme change only update
 * options and data, so the chart is never torn down mid-session.
 */
export function CandleChart({
  bars,
  menu = [],
  indicators = [],
}: {
  bars: Bars;
  /** The indicator menu, and the keys switched on (their values come with the bars). */
  menu?: IndicatorInfo[];
  indicators?: string[];
}) {
  const el = useRef<HTMLDivElement>(null);
  const handles = useRef<Handles>();
  const layer = useRef<IndicatorLayer>();
  const fitted = useRef<Bars>();
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
      timeScale: { timeVisible: true, secondsVisible: false },
      localization: {
        priceFormatter: (p: number) => p.toLocaleString("en-AU", { maximumFractionDigits: 2 }),
      },
      crosshair: { mode: 0 },
    });
    const candles = chart.addSeries(CandlestickSeries, { borderVisible: false });
    const volume = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.2 } });
    handles.current = { chart, candles, volume };
    layer.current = new IndicatorLayer(chart, [440]);

    return () => {
      handles.current = undefined;
      layer.current = undefined;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const h = handles.current;
    if (!h) return;

    const up = cssVar("--up");
    const down = cssVar("--down");
    const line = cssVar("--line");
    h.chart.applyOptions({
      layout: { textColor: cssVar("--text-3") },
      grid: { vertLines: { visible: false }, horzLines: { color: line } },
      rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line },
      crosshair: {
        vertLine: { color: cssVar("--text-3"), labelBackgroundColor: cssVar("--text") },
        horzLine: { color: cssVar("--text-3"), labelBackgroundColor: cssVar("--text") },
      },
    });
    h.candles.applyOptions({ upColor: up, downColor: down, wickUpColor: up, wickDownColor: down });

    h.candles.setData(
      bars.time.map((t, i) => ({
        time: t as UTCTimestamp,
        open: bars.open[i]!,
        high: bars.high[i]!,
        low: bars.low[i]!,
        close: bars.close[i]!,
      })),
    );
    const upVol = withAlpha(up, 0.28);
    const downVol = withAlpha(down, 0.28);
    h.volume.setData(
      bars.time.map((t, i) => ({
        time: t as UTCTimestamp,
        value: bars.volume[i]!,
        color: bars.close[i]! >= bars.open[i]! ? upVol : downVol,
      })),
    );
    // Zoom to fit only for new bars, never on a theme switch: keep the user's zoom.
    if (fitted.current !== bars) {
      h.chart.timeScale().fitContent();
      fitted.current = bars;
    }
  }, [bars, theme]);


  // Indicators are drawn after the bars, and redrawn when switched on or off. Drawing them
  // never refits the zoom.
  useEffect(() => {
    layer.current?.draw(indicatorPlan(menu, indicators, 1), bars.time, bars.indicators);
  }, [bars, menu, indicators, theme]);
  // Each indicator pane adds height rather than squeezing the price pane.
  const extra = ownPanes(menu, indicators) * INDICATOR_PANE_PX;
  return <div ref={el} className="chart" style={{ "--indicator-extra": `${extra}px` } as CSSProperties} />;
}
