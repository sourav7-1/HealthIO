import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HealthIOSplash } from "./HealthIOSplash";

describe("HealthIOSplash", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("renders with accessible status role and brand elements", () => {
    render(<HealthIOSplash duration={2200} />);
    const status = screen.getByRole("status");
    expect(status).toBeInTheDocument();
    expect(status).toHaveAttribute(
      "aria-label",
      "HealthIO is loading and preparing your healthcare environment",
    );
    expect(screen.getByText("Health")).toBeInTheDocument();
    expect(screen.getByText("IO")).toBeInTheDocument();
    expect(
      screen.getByText("Intelligent Healthcare & Medication Management Platform"),
    ).toBeInTheDocument();
  });

  it("calls onComplete after target duration and exit transition", () => {
    const onComplete = vi.fn();
    render(<HealthIOSplash duration={2000} onComplete={onComplete} />);

    expect(onComplete).not.toHaveBeenCalled();

    // Advance through animation and exit transition
    act(() => {
      vi.advanceTimersByTime(2400);
    });

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("allows user to skip immediately with Skip button", () => {
    const onComplete = vi.fn();
    render(<HealthIOSplash duration={2200} onComplete={onComplete} canSkip={true} />);

    const skipButton = screen.getByRole("button", { name: "Skip opening animation" });
    expect(skipButton).toBeInTheDocument();

    fireEvent.click(skipButton);

    // Advance exit transition
    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("allows user to skip using Escape key", () => {
    const onComplete = vi.fn();
    render(<HealthIOSplash duration={2200} onComplete={onComplete} canSkip={true} />);

    const status = screen.getByRole("status");
    fireEvent.keyDown(status, { key: "Escape" });

    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("handles reduced motion gracefully with quick completion", () => {
    const onComplete = vi.fn();
    render(<HealthIOSplash duration={2200} reducedMotion={true} onComplete={onComplete} />);

    // In reduced motion, it transitions much faster
    act(() => {
      vi.advanceTimersByTime(900);
    });

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("renders dark theme styling correctly", () => {
    const { container } = render(<HealthIOSplash theme="dark" />);
    const overlay = container.firstChild as HTMLElement;
    expect(overlay).toHaveClass("bg-[#060b14]");
  });

  it("hides tagline when showTagline is false", () => {
    render(<HealthIOSplash showTagline={false} />);
    expect(
      screen.queryByText("Intelligent Healthcare & Medication Management Platform"),
    ).not.toBeInTheDocument();
  });
});

