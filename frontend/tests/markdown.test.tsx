import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Markdown } from "../src/components/Markdown";

describe("markdown display boundary", () => {
  it("filters hidden reasoning and selector JSON before rendering", () => {
    const { container } = render(
      <Markdown source={'<think>internal</think>visible'} />,
    );
    expect(container.textContent).toContain("visible");
    expect(container.textContent).not.toContain("internal");
    const selector = render(<Markdown source={'{"tools":["search"]}'} />);
    expect(selector.container.textContent).not.toContain('"tools"');
  });

  it("keeps unsafe links and remote images non-interactive", () => {
    const { container } = render(
      <Markdown source={'[危险链接](javascript:alert(1)) ![远程图片](https://example.com/a.png)'} />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("图片：远程图片");
  });
});
