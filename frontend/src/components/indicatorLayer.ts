import {
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type SeriesType,
  type UTCTimestamp,
} from "lightweight-charts";
import type { IndicatorLines } from "../api";
import { INDICATOR_PANE_PX, type PlanLine } from "../indicators";
import { cssVar } from "../theme";

const number = (x: number) => x.toLocaleString("en-AU", { maximumFractionDigits: 2 });
const percent = (x: number) => `${(x * 100).toFixed(0)}%`;

/**
 * Indicator series on a chart whose own panes have the heights `basePx` (pane 0 first).
 * Each `draw` replaces the previous indicators (series and the panes they added) with the
 * plan's, so a chart keeps one lifetime while indicators are switched on and off. Null
 * values are gaps. Pane sizes are set as stretch factors in pixels (relative weights), so
 * they stay right however the chart is resized as panes come and go.
 */
export class IndicatorLayer {
  private series: ISeriesApi<SeriesType>[] = [];
  private readonly basePanes: number;

  constructor(
    private readonly chart: IChartApi,
    private readonly basePx: number[],
  ) {
    this.basePanes = basePx.length;
  }

  draw(plan: PlanLine[], time: number[], values: IndicatorLines | undefined): void {
    this.clear();
    const panes = new Set<number>();
    for (const p of plan) {
      const color = cssVar(p.color);
      const format = { type: "custom" as const, formatter: p.percent ? percent : number, minMove: 0.0001 };
      const common = { priceLineVisible: false, lastValueVisible: false, priceFormat: format, title: "" };
      const series: ISeriesApi<SeriesType> =
        p.kind === "histogram"
          ? this.chart.addSeries(HistogramSeries, { ...common, color }, p.pane)
          : this.chart.addSeries(
              LineSeries,
              { ...common, color, lineWidth: 1, lineStyle: p.dashed ? LineStyle.Dashed : LineStyle.Solid },
              p.pane,
            );
      const line = values?.[p.key]?.[p.line] ?? [];
      series.setData(
        time.map((t, i) => {
          const v = line[i];
          return v === null || v === undefined ? { time: t as UTCTimestamp } : { time: t as UTCTimestamp, value: v };
        }),
      );
      for (const g of p.guides) {
        series.createPriceLine({
          price: g,
          color: cssVar("--line-strong"),
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: false,
        });
      }
      this.series.push(series);
      if (p.pane >= this.basePanes) panes.add(p.pane);
    }
    const all = this.chart.panes();
    this.basePx.forEach((px, i) => all[i]?.setStretchFactor(px));
    for (const pane of panes) all[pane]?.setStretchFactor(INDICATOR_PANE_PX);
  }

  clear(): void {
    for (const s of this.series) this.chart.removeSeries(s);
    this.series = [];
    // Remove the panes indicators added, highest first so indexes stay valid.
    for (let i = this.chart.panes().length - 1; i >= this.basePanes; i--) this.chart.removePane(i);
  }
}
