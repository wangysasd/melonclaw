import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { ResultBlock } from "../lib/resultBlocks";

echarts.use([LineChart, BarChart, GridComponent, LegendComponent, TooltipComponent, AriaComponent, SVGRenderer]);
type Chart = Extract<ResultBlock, { type: "chart" }>;
/** 所有 option 由代码构造；标签只是字符串，不采用 HTML tooltip。 */
export default function ResultChartCanvas({ chart }: { chart: Chart }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const instance = echarts.init(ref.current, null, { renderer: "svg" });
    instance.setOption({
      animation: false,
      color: ["#0071e3", "#29976b", "#b76d16", "#8860b4", "#c35765", "#348b9c", "#69738a", "#8a8242"],
      textStyle: { fontFamily: "system-ui, sans-serif", fontSize: 14 },
      aria: { enabled: true, label: { description: `${chart.title}，${chart.chart === "line" ? "折线图" : "柱状图"}；横轴：${chart.x_label}，单位：${chart.unit}。精确数值见下方绘图数据表。` } },
      tooltip: { trigger: "axis", renderMode: "richText", confine: true },
      legend: { type: "scroll", bottom: 0, textStyle: { fontSize: 14 } },
      grid: { top: 24, left: 12, right: 16, bottom: 58, containLabel: true },
      xAxis: { type: "category", data: chart.rows.map((row) => row[0]), axisLabel: { hideOverlap: true, fontSize: 14 } },
      yAxis: { type: "value", scale: false, axisLabel: { fontSize: 14 } },
      series: chart.series.map((name, index) => ({ name, type: chart.chart, data: chart.rows.map((row) => row[index + 1]), connectNulls: false, symbolSize: 7, barMaxWidth: 40 })),
    });
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(ref.current);
    return () => { observer.disconnect(); instance.dispose(); };
  }, [chart]);
  return <div ref={ref} className="result-chart-canvas" role="img" aria-label={`${chart.title}，${chart.chart === "line" ? "折线图" : "柱状图"}；横轴：${chart.x_label}，单位：${chart.unit}。精确数值见下方绘图数据表。`} />;
}
