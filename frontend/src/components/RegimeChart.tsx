import {
  ColorType,
  HistogramSeries,
  LineSeries,
  PriceScaleMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { RegimeDetail } from "../api";
import { probabilityLines, shadeAlpha, shading, stateColor } from "../regimes";
import { cssVar, useTheme } from "../theme";

interface Handles {
  chart: IChartApi;
  price: ISeriesApi<"Line">;
  shade: ISeriesApi<"Histogram">;
  probabilities: ISeriesApi<"Line">[];
}

const money = (p: number) => p.toLocaleString("en-AU", { maximumFractionDigits: 2 });
const pct = (p: number) => `${Math.round(p * 100)}%`;

function withAlpha(hex: string, alpha: number): string {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/**
 * The close on a log scale, each day shaded by the state the model believed that day,
 * and each state's probability in a pane underneath. Shading is a full-height histogram
 * on its own hidden scale. Same lifetime model as the other charts: one chart per mount,
 * and the view zooms to fit only when the fit itself changes.
 */
export function RegimeChart({ detail }: { detail: RegimeDetail }) {
  const el = useRef<HTMLDivElement>(null);
  const handles = useRef<Handles>();
  const fitted = useRef<RegimeDetail>();
  const { theme } = useTheme();
  const states = detail.states.length;

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
      timeScale: { timeVisible: false, minBarSpacing: 0.001 },
      crosshair: { mode: 0 },
    });
    const common = { lastValueVisible: false, priceLineVisible: false };
    const shade = chart.addSeries(HistogramSeries, { ...common, priceScaleId: "shade", base: 0 });
    shade.priceScale().applyOptions({ visible: false, scaleMargins: { top: 0, bottom: 0 } });
    const price = chart.addSeries(LineSeries, {
      ...common,
      lineWidth: 2,
      priceFormat: { type: "custom", formatter: money, minMove: 0.01 },
    });
    price.priceScale().applyOptions({ mode: PriceScaleMode.Logarithmic });
    const probabilities = Array.from({ length: states }, () =>
      chart.addSeries(
        LineSeries,
        {
          ...common,
          lineWidth: 1,
          priceFormat: { type: "custom", formatter: pct, minMove: 0.01 },
          // Probabilities: always the full 0-100% range, never auto-scaled.
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }),
        },
        1,
      ),
    );
    chart.panes()[1]?.setHeight(140);
    handles.current = { chart, price, shade, probabilities };
    return () => {
      handles.current = undefined;
      chart.remove();
    };
  }, [states]);

  useEffect(() => {
    const h = handles.current;
    if (!h) return;
    const line = cssVar("--line");
    const text = cssVar("--text");
    const muted = cssVar("--text-3");
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
    h.price.applyOptions({ color: text });
    h.price.setData(detail.time.map((t, i) => ({ time: t as UTCTimestamp, value: detail.close[i]! })));
    h.shade.setData(
      shading(detail).map((d) => ({
        time: d.time as UTCTimestamp,
        value: 1,
        color: withAlpha(cssVar(stateColor(d.state)), shadeAlpha(d.state)),
      })),
    );
    probabilityLines(detail).forEach((points, k) => {
      h.probabilities[k]?.applyOptions({ color: cssVar(stateColor(k)) });
      h.probabilities[k]?.setData(points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    });
    if (fitted.current !== detail) {
      h.chart.timeScale().fitContent();
      fitted.current = detail;
    }
  }, [detail, theme]);

  return <div ref={el} className="chart regime-chart" />;
}
