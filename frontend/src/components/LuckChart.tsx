import { ColorType, LineSeries, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { luckReadout } from "../researchView";
import { cssVar, useTheme } from "../theme";

type Point = { time: number; value: number; trials: number };

/**
 * The luck bar over time: one line, the annual Sharpe the best of all trials so far would
 * reach by luck. A single series, so the section title names it and there is no legend;
 * the trial count isn't on a second axis but in the readout, which follows the cursor and
 * otherwise shows the latest point.
 */
export function LuckChart({ points }: { points: Point[] }) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi>();
  const line = useRef<ISeriesApi<"Line">>();
  const { theme } = useTheme();
  const latest = points[points.length - 1];
  const [hovered, setHovered] = useState<Point | undefined>();

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, sans-serif",
        fontSize: 11,
        attributionLogo: false,
      },
      timeScale: { timeVisible: false, minBarSpacing: 0.001 },
      crosshair: { mode: 0 },
      handleScroll: false,
      handleScale: false,
    });
    const s = c.addSeries(LineSeries, {
      lineWidth: 2,
      lastValueVisible: false,
      priceLineVisible: false,
      priceFormat: { type: "custom", formatter: (v: number) => v.toFixed(2), minMove: 0.01 },
    });
    chart.current = c;
    line.current = s;
    return () => {
      chart.current = undefined;
      line.current = undefined;
      c.remove();
    };
  }, []);

  useEffect(() => {
    const c = chart.current;
    const s = line.current;
    if (!c || !s) return;
    const rule = cssVar("--line");
    const text = cssVar("--text");
    const muted = cssVar("--text-3");
    c.applyOptions({
      layout: { textColor: muted },
      grid: { vertLines: { visible: false }, horzLines: { color: rule } },
      rightPriceScale: { borderColor: rule },
      timeScale: { borderColor: rule },
      crosshair: {
        vertLine: { color: muted, labelBackgroundColor: text },
        horzLine: { color: muted, labelBackgroundColor: text },
      },
    });
    s.applyOptions({ color: text });
    s.setData(points.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    c.timeScale().fitContent();
    const byTime = new Map(points.map((p) => [p.time, p]));
    const onMove = (param: { time?: unknown }) => setHovered(byTime.get(param.time as number));
    c.subscribeCrosshairMove(onMove);
    return () => c.unsubscribeCrosshairMove(onMove);
  }, [points, theme]);

  const shown = hovered ?? latest;
  return (
    <div>
      <p className="small secondary" aria-live="polite">
        {shown ? luckReadout(shown) : "No trials logged yet."}
      </p>
      <div ref={el} style={{ height: 220 }} role="img" aria-label="The luck bar over time" />
    </div>
  );
}
