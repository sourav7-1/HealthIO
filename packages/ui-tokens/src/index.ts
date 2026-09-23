/**
 * Shared design tokens for web and mobile. Phase 22 expands these into the full design system.
 * Status colours are chosen to stay distinguishable for common colour-vision deficiencies.
 */
export const colors = {
  brand: { 50: "#eef8f6", 100: "#d5efe9", 500: "#11806f", 600: "#0c6b5d", 700: "#0a574c" },
  neutral: { 0: "#ffffff", 50: "#f7f8f8", 200: "#e3e6e6", 500: "#6b7372", 800: "#232827", 900: "#141817" },
  status: { success: "#1f7a3d", warning: "#a15c00", danger: "#b42318", info: "#1d5fa8" },
} as const;

export const spacing = { 0: 0, 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, 10: 40, 12: 48 } as const;

export const radius = { sm: 6, md: 10, lg: 16, full: 9999 } as const;

/** Minimum body size is 16px; large-text mode (for older users) scales everything by 1.25. */
export const fontSize = { xs: 12, sm: 14, base: 16, lg: 18, xl: 22, "2xl": 28, "3xl": 36 } as const;
export const largeTextScale = 1.25;

export const minTouchTarget = 44;
