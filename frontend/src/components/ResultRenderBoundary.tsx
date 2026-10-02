import { Component, type ReactNode } from "react";

/** 局部可视化失败不隐藏同一结果的数据表和来源。 */
export class ResultRenderBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <p role="status">图表暂时无法显示，可展开下方数据表查看或下载。</p> : this.props.children; }
}
