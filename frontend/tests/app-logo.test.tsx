import { render } from "@testing-library/react";
import { expect, it } from "vitest";
import { AppLogo } from "../src/components/AppLogo";

it("renders the first Unicode character and uppercases Latin initials", () => {
  const { container, rerender } = render(<AppLogo name="  因子研究  " />);
  const logo = container.querySelector(".app-logo");
  expect(logo?.textContent).toBe("因");

  rerender(<AppLogo name="  github  " />);
  expect(logo?.textContent).toBe("G");
});

it("uses a question mark for an empty name and a stable color for the same name", () => {
  const { container, rerender } = render(<AppLogo name="   " />);
  const logo = container.querySelector(".app-logo");
  expect(logo?.textContent).toBe("?");

  rerender(<AppLogo name={null} />);
  expect(logo?.textContent).toBe("?");
  rerender(<AppLogo />);
  expect(logo?.textContent).toBe("?");

  rerender(<AppLogo name="GitHub" />);
  const firstBackground = (logo as HTMLElement).style.backgroundColor;
  rerender(<AppLogo name=" GitHub " />);
  expect((logo as HTMLElement).style.backgroundColor).toBe(firstBackground);
});
