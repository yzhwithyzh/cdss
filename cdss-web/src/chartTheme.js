// ECharts 主题：与设计令牌一致（深青 / 赭石 / 灰绿）
import * as echarts from "echarts";
import { onBeforeUnmount, onMounted } from "vue";

export const PALETTE = ["#1d6f7a", "#c77d34", "#6d9a7b", "#b5473a", "#7486a8", "#9a7b55", "#4c97a1", "#d6aa6c", "#8a6fa3", "#5f7f4a"];

echarts.registerTheme("kb", {
  color: PALETTE,
  backgroundColor: "transparent",
  textStyle: { fontFamily: "-apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif", color: "#4a5a66" },
  legend: { textStyle: { color: "#4a5a66" }, itemWidth: 12, itemHeight: 12, icon: "roundRect" },
  tooltip: {
    backgroundColor: "rgba(20, 36, 44, 0.92)", borderWidth: 0, textStyle: { color: "#fff", fontSize: 12 },
    padding: [8, 12], extraCssText: "border-radius: 8px; box-shadow: 0 4px 16px rgba(10,30,40,.18);",
  },
  categoryAxis: {
    axisLine: { lineStyle: { color: "#e1e8ea" } }, axisTick: { show: false },
    axisLabel: { color: "#83939c", fontSize: 11 }, splitLine: { show: false },
  },
  valueAxis: {
    axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: "#83939c", fontSize: 11 },
    splitLine: { lineStyle: { color: "#edf2f3" } },
  },
  line: { smooth: true, symbol: "circle", symbolSize: 5, lineStyle: { width: 2.5 }, itemStyle: { borderWidth: 2, borderColor: "#fff" } },
  bar: { itemStyle: { borderRadius: [0, 4, 4, 0] }, barMaxWidth: 20 },
  pie: { itemStyle: { borderColor: "#fff", borderWidth: 2 }, label: { color: "#4a5a66", fontSize: 11 } },
});

// 组件里统一用这个：自动 resize，卸载时销毁
export function useChart(elRef) {
  let chart = null;
  const onResize = () => chart?.resize();
  onMounted(() => window.addEventListener("resize", onResize));
  onBeforeUnmount(() => { window.removeEventListener("resize", onResize); chart?.dispose(); chart = null; });
  return (option) => {
    if (!elRef.value) return;
    chart = chart || echarts.init(elRef.value, "kb");
    chart.setOption(option, true);
    return chart;
  };
}

export default echarts;
