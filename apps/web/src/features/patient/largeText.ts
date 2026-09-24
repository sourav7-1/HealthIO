/** Per-device "larger text" preference. Stored locally; storage may be unavailable. */
import { useSyncExternalStore } from "react";

const KEY = "hio.largeText";
const listeners = new Set<() => void>();

function read(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

/** Apply the saved preference to <html>; call once at start-up. */
export function applyLargeText(on = read()): void {
  document.documentElement.classList.toggle("large-text", on);
}

export function setLargeText(on: boolean): void {
  try {
    if (on) localStorage.setItem(KEY, "1");
    else localStorage.removeItem(KEY);
  } catch {
    // Not saved, but still applied for this visit.
  }
  applyLargeText(on);
  listeners.forEach((l) => l());
}

export function useLargeText(): boolean {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => document.documentElement.classList.contains("large-text"),
    () => false,
  );
}
