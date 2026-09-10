import { XMarkdown, type XMarkdownProps } from "@ant-design/x-markdown";
import Latex from "@ant-design/x-markdown/plugins/Latex";

const config = {
  extensions: Latex({ katexOptions: { trust: false, throwOnError: false, strict: "error", maxExpand: 1000, maxSize: 20 } }),
};

export default function MathMarkdown(props: XMarkdownProps) {
  return <XMarkdown {...props} config={config} />;
}
