import {
  AreaSeries,
  ColorType,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { EquitySeries } from "../api";
import { cssVar, useTheme } from "../theme";

interface Handles {
  chart: IChartApi;
  equity: ISeriesApi<"Line">;
  benchmark: ISeriesApi<"Line">;
  drawdown: ISeriesApi<"Area">;
  benchmarkDrawdown: ISeriesApi<"Line">;
}

const pct = (p: number) => `${(p * 100).toFixed(0)}%`;
const money2 = (p: number) => p.toLocaleString("en-AU", { maximumFractionDigits: 2 });

function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/**
 * Strategy equity against the benchmark, with both drawdowns in a pane underneath.
 * Same lifetime model as CandleChart: one chart per mount; data or theme changes only
 * update options and series data.
 */
export function EquityChart({ equity, benchmark }: { equity: EquitySeries; benchmark: EquitySeries }) {
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
      // The default minimum bar spacing (0.5px) can't fit years of 4h bars, so the
      // chart would silently show only the most recent part. Allow it to compress.
      timeScale: { timeVisible: false, minBarSpacing: 0.001 },
      crosshair: { mode: 0 },
    });
    // Each pane's axis takes its number format from its series: money on top, % below.
    const common = { lastValueVisible: false, priceLineVisible: false };
    const money = { type: "custom" as const, formatter: money2, minMove: 0.01 };
    const benchmarkLine = chart.addSeries(LineSeries, {
      ...common,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceFormat: money,
    });
    const equityLine = chart.addSeries(LineSeries, { ...common, lineWidth: 2, priceFormat: money });
    const pctFormat = { type: "custom" as const, formatter: pct, minMove: 0.0001 };
    const drawdown = chart.addSeries(AreaSeries, { ...common, lineWidth: 1, priceFormat: pctFormat }, 1);
    const benchmarkDrawdown = chart.addSeries(
      LineSeries,
      { ...common, lineWidth: 1, lineStyle: LineStyle.Dashed, priceFormat: pctFormat },
      1,
    );
    chart.panes()[1]?.setHeight(130);
    handles.current = { chart, equity: equityLine, benchmark: benchmarkLine, drawdown, benchmarkDrawdown };
    return () => {
      handles.current = undefined;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const h = handles.current;
    if (!h) return;
    const line = cssVar("--line");
    const text = cssVar("--text");
    const muted = cssVar("--text-3");
    const down = cssVar("--down");
    h.chart.applyOptions({
      layout: { textColor: muted, panes: { separatorColor: line } },
      grid: { vertLines: { visible: false }, horzLines: { color: line } },
      rightPriceScale: { borderColor: line },
      timeScale: { borderColor: line },
      crosshair: {
        vertLine: { color: muted, labelBackgroundColor: text },
        horzLine: { color: muted, labelBackgroundColor: text },
      },
    });
    h.equity.applyOptions({ color: text });
    h.benchmark.applyOptions({ color: muted });
    h.drawdown.applyOptions({ lineColor: down, topColor: withAlpha(down, 0.05), bottomColor: withAlpha(down, 0.3) });
    h.benchmarkDrawdown.applyOptions({ color: muted });

    const points = (s: EquitySeries, key: "equity" | "drawdown") =>
      s.time.map((t, i) => ({ time: t as UTCTimestamp, value: s[key][i]! }));
    h.equity.setData(points(equity, "equity"));
    h.benchmark.setData(points(benchmark, "equity"));
    h.drawdown.setData(points(equity, "drawdown"));
    h.benchmarkDrawdown.setData(points(benchmark, "drawdown"));
    h.chart.timeScale().fitContent();
  }, [equity, benchmark, theme]);

  return <div ref={el} className="chart equity-chart" />;
}
