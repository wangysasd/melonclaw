import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { ImageLightbox } from "../src/components/ImageLightbox";

describe("image lightbox", () => {
  it("closes on Escape and on backdrop click but not on the image itself", () => {
    const onClose = vi.fn();
    render(
      <ImageLightbox src="/api/attachments/a1/content" alt="截图.png" onClose={onClose} />,
    );

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("img", { name: "截图.png" }));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("dialog", { name: "截图.png" }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
