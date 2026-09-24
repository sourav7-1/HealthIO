import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Logo, LogoIcon } from "./Logo";

describe("Logo", () => {
  it("renders the vector logo with Health and IO", () => {
    render(<Logo />);
    expect(screen.getByText("Health")).toBeInTheDocument();
    expect(screen.getByText("IO")).toBeInTheDocument();
  });

  it("renders with tagline when requested", () => {
    render(<Logo showTagline />);
    expect(
      screen.getByText("Intelligent Healthcare & Medication Management Platform"),
    ).toBeInTheDocument();
  });

  it("renders mark only variant", () => {
    const { container } = render(<Logo variant="mark" />);
    expect(container.querySelector("svg")).toBeInTheDocument();
    expect(screen.queryByText("Health")).not.toBeInTheDocument();
  });

  it("renders image variant", () => {
    render(<Logo variant="image" />);
    const img = screen.getByRole("img", { name: "HealthIO" });
    expect(img).toBeInTheDocument();
    expect(img).toHaveAttribute("src", "/logo.png");
  });

  it("renders LogoIcon with accessible attributes", () => {
    const { container } = render(<LogoIcon className="test-class" />);
    const svg = container.querySelector("svg");
    expect(svg).toBeInTheDocument();
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).toHaveClass("test-class");
  });
});
