import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
} from "react";
import { cn } from "./primitives";

export interface HealthIOSplashProps {
  /**
   * Theme mode for the splash screen:
   * - "light": clean healthcare off-white background with dark navy wordmark
   * - "dark": deep clinical navy background with light wordmark
   * - "system": follows system prefers-color-scheme or root .dark class
   * Default: "system"
   */
  theme?: "light" | "dark" | "system";

  /**
   * Total animation duration in milliseconds.
   * Default: 2200 (within 1.8s–2.5s target)
   */
  duration?: number;

  /**
   * Callback fired when the animation completes and the exit transition finishes.
   */
  onComplete?: () => void;

  /**
   * Whether the animation should automatically start playing upon mounting.
   * Default: true
   */
  autoPlay?: boolean;

  /**
   * Explicitly override reduced motion preference.
   * When true, skips stroke tracing and shows a fast crossfade.
   * Default: auto-detects from prefers-reduced-motion media query.
   */
  reducedMotion?: boolean;

  /**
   * Whether to display the tagline ("Intelligent Healthcare & Medication Management Platform").
   * Default: true (responsively hidden on screens < 460px)
   */
  showTagline?: boolean;

  /**
   * Whether the user can skip the animation by clicking, tapping, or pressing Esc/Space.
   * Default: true
   */
  canSkip?: boolean;

  /**
   * Custom CSS class name for the overlay container.
   */
  className?: string;
}

function usePrefersReducedMotion(override?: boolean): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (
        typeof override === "boolean" ||
        typeof window === "undefined" ||
        typeof window.matchMedia !== "function"
      ) {
        return () => {};
      }
      const mql = window.matchMedia("(prefers-reduced-motion: reduce)");
      mql.addEventListener("change", notify);
      return () => mql.removeEventListener("change", notify);
    },
    () => {
      if (typeof override === "boolean") return override;
      if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
        return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      }
      return false;
    },
    () => (typeof override === "boolean" ? override : false),
  );
}

function useIsDarkTheme(theme: "light" | "dark" | "system"): boolean {
  return useSyncExternalStore(
    (notify) => {
      if (
        theme !== "system" ||
        typeof window === "undefined" ||
        typeof window.matchMedia !== "function"
      ) {
        return () => {};
      }
      const mql = window.matchMedia("(prefers-color-scheme: dark)");
      mql.addEventListener("change", notify);
      return () => mql.removeEventListener("change", notify);
    },
    () => {
      if (theme === "dark") return true;
      if (theme === "light") return false;
      if (typeof document !== "undefined" && document.documentElement.classList.contains("dark")) {
        return true;
      }
      if (typeof window !== "undefined" && typeof window.matchMedia === "function") {
        return window.matchMedia("(prefers-color-scheme: dark)").matches;
      }
      return false;
    },
    () => theme === "dark",
  );
}

/**
 * HealthIO branded opening animation.
 * Features the official HealthIO logo mark (abstract H-shaped icon with heartbeat pulse,
 * medical cross, and signature blue-to-teal gradient).
 */
export function HealthIOSplash({
  theme = "system",
  duration = 2200,
  onComplete,
  autoPlay = true,
  reducedMotion,
  showTagline = true,
  canSkip = true,
  className,
}: HealthIOSplashProps) {
  const uid = useId().replace(/:/g, "");
  const [phase, setPhase] = useState<"running" | "exiting" | "done">(autoPlay ? "running" : "done");
  const effectiveReducedMotion = usePrefersReducedMotion(reducedMotion);
  const isDark = useIsDarkTheme(theme);
  const exitTimerRef = useRef<number | null>(null);

  // Handle completion
  const handleExit = useCallback(() => {
    setPhase("exiting");
    if (exitTimerRef.current !== null) {
      window.clearTimeout(exitTimerRef.current);
    }
    exitTimerRef.current = window.setTimeout(() => {
      setPhase("done");
      onComplete?.();
    }, 280);
  }, [onComplete]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (exitTimerRef.current !== null) {
        window.clearTimeout(exitTimerRef.current);
      }
    };
  }, []);

  // Skip handler (click or keyboard)
  const handleSkip = useCallback(() => {
    if (!canSkip || phase === "exiting" || phase === "done") return;
    handleExit();
  }, [canSkip, phase, handleExit]);

  // Keyboard navigation for accessibility
  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      if (e.key === "Escape" || e.key === " " || e.key === "Enter") {
        e.preventDefault();
        handleSkip();
      }
    },
    [handleSkip],
  );

  // Animation timeline sequencer
  useEffect(() => {
    if (!autoPlay || phase !== "running") return;

    if (effectiveReducedMotion) {
      // Reduced motion: show clean logo immediately, hold briefly, then exit
      const exitTimer = window.setTimeout(() => {
        handleExit();
      }, 550);
      return () => window.clearTimeout(exitTimer);
    }

    // Scale timeline according to target duration (baseline: 2200ms)
    const holdDuration = Math.max(300, Math.round(duration * 0.18));
    const runDuration = duration - holdDuration;

    const exitTimer = window.setTimeout(() => {
      handleExit();
    }, runDuration + holdDuration);

    return () => window.clearTimeout(exitTimer);
  }, [autoPlay, duration, effectiveReducedMotion, handleExit, phase]);

  if (phase === "done") return null;

  // Animation speed ratio relative to 2200ms baseline
  const speedRatio = duration / 2200;
  const tLeadin = `${Math.round(550 * speedRatio)}ms`;
  const tHReveal = `${Math.round(550 * speedRatio)}ms`;
  const tCross = `${Math.round(300 * speedRatio)}ms`;
  const tHeartbeat = `${Math.round(550 * speedRatio)}ms`;
  const tWordmark = `${Math.round(480 * speedRatio)}ms`;
  const tTagline = `${Math.round(420 * speedRatio)}ms`;

  const dLeadinStart = `${Math.round(200 * speedRatio)}ms`;
  const dHStart = `${Math.round(650 * speedRatio)}ms`;
  const dCrossStart = `${Math.round(1050 * speedRatio)}ms`;
  const dWordmarkStart = `${Math.round(1200 * speedRatio)}ms`;
  const dTaglineStart = `${Math.round(1400 * speedRatio)}ms`;

  return (
    <div
      role="status"
      aria-label="HealthIO is loading and preparing your healthcare environment"
      tabIndex={canSkip ? 0 : -1}
      onKeyDown={canSkip ? handleKeyDown : undefined}
      onClick={canSkip ? handleSkip : undefined}
      className={cn(
        "fixed inset-0 z-50 flex items-center justify-center select-none overflow-hidden outline-none cursor-default",
        isDark ? "bg-[#060b14] text-white" : "bg-[#f8fafc] text-[#0a1936]",
        phase === "exiting" && "pointer-events-none transition-all duration-300 ease-out opacity-0 scale-[1.015]",
        className,
      )}
    >
      <style>{`
        /* Scoped CSS animations for HealthIO Opening Sequence */
        @keyframes hioAmbientGlow_${uid} {
          0% { opacity: 0; transform: scale(0.65); }
          45% { opacity: 1; transform: scale(1); }
          100% { opacity: 0.85; transform: scale(1.1); }
        }

        @keyframes hioDrawLeadIn_${uid} {
          0% { stroke-dashoffset: 270; opacity: 0; }
          20% { opacity: 1; }
          100% { stroke-dashoffset: 0; opacity: 1; }
        }

        @keyframes hioFadeLeadIn_${uid} {
          0% { opacity: 1; }
          100% { opacity: 0; }
        }

        @keyframes hioRevealH_${uid} {
          0% {
            opacity: 0;
            transform: scale(0.95);
            filter: drop-shadow(0 0 0px rgba(0, 98, 234, 0));
          }
          100% {
            opacity: 1;
            transform: scale(1);
            filter: drop-shadow(0 4px 16px rgba(0, 98, 234, ${isDark ? "0.28" : "0.14"}));
          }
        }

        @keyframes hioDrawHeartbeat_${uid} {
          0% { stroke-dashoffset: 270; }
          100% { stroke-dashoffset: 0; }
        }

        @keyframes hioRevealCross_${uid} {
          0% { opacity: 0; transform: scale(0.75); }
          100% { opacity: 1; transform: scale(1); }
        }

        @keyframes hioExpandTextWrap_${uid} {
          0% {
            max-width: 0px;
            opacity: 0;
            transform: translateX(-12px);
          }
          30% {
            opacity: 0.3;
          }
          100% {
            max-width: 420px;
            opacity: 1;
            transform: translateX(0);
          }
        }

        @keyframes hioRevealTagline_${uid} {
          0% { opacity: 0; transform: translateY(4px); }
          100% { opacity: 1; transform: translateY(0); }
        }
      `}</style>

      {/* Ambient medical glow in background */}
      <div
        className="absolute pointer-events-none rounded-full"
        style={{
          width: "min(680px, 95vw)",
          height: "min(680px, 95vw)",
          background: isDark
            ? "radial-gradient(circle, rgba(0, 98, 234, 0.16) 0%, rgba(0, 188, 162, 0.08) 42%, transparent 72%)"
            : "radial-gradient(circle, rgba(0, 98, 234, 0.07) 0%, rgba(0, 188, 162, 0.04) 40%, transparent 70%)",
          animation: effectiveReducedMotion
            ? "none"
            : `hioAmbientGlow_${uid} ${duration}ms cubic-bezier(0.16, 1, 0.3, 1) forwards`,
        }}
      />

      {/* Central Brand Lockup Stage */}
      <div className="relative z-10 flex items-center justify-center max-w-[94vw] px-4">
        {/* Incoming ECG Pulse Line (Scene 2: Draws from left toward center) */}
        {!effectiveReducedMotion && (
          <div
            className="absolute right-full top-1/2 -translate-y-1/2 w-[220px] sm:w-[260px] h-[100px] pointer-events-none overflow-visible hidden xs:block"
            aria-hidden="true"
          >
            <svg
              viewBox="0 0 260 100"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              className="w-full h-full"
            >
              <defs>
                <linearGradient id={`leadin-grad-${uid}`} x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#0062ea" stopOpacity="0" />
                  <stop offset="65%" stopColor="#007bf3" stopOpacity="0.8" />
                  <stop offset="100%" stopColor="#0062ea" stopOpacity="1" />
                </linearGradient>
                <filter id={`glow-${uid}`} x="-20%" y="-20%" width="140%" height="140%">
                  <feGaussianBlur stdDeviation="2" result="blur" />
                  <feMerge>
                    <feMergeNode in="blur" />
                    <feMergeNode in="SourceGraphic" />
                  </feMerge>
                </filter>
              </defs>

              {/* Lead-in pulse path */}
              <path
                d="M 0 50 L 130 50 L 150 44 L 165 55 L 180 50 L 260 50"
                stroke={`url(#leadin-grad-${uid})`}
                strokeWidth="5"
                strokeLinecap="round"
                strokeLinejoin="round"
                filter={`url(#glow-${uid})`}
                style={{
                  strokeDasharray: 270,
                  strokeDashoffset: 270,
                  animation: `hioDrawLeadIn_${uid} ${tLeadin} cubic-bezier(0.4, 0, 0.2, 1) ${dLeadinStart} forwards, hioFadeLeadIn_${uid} 350ms ease-out ${dHStart} forwards`,
                }}
              />
            </svg>
          </div>
        )}

        {/* Brand Lockup: [HealthIO Icon] + HealthIO Wordmark */}
        <div className="flex items-center gap-3 sm:gap-4.5">
          {/* HealthIO Official H-Mark */}
          <div className="relative w-[68px] h-[68px] sm:w-[84px] sm:h-[84px] shrink-0">
            <svg
              viewBox="0 0 176 177"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
              className="w-full h-full"
              aria-hidden="true"
            >
              <defs>
                <linearGradient id={`hio-grad-${uid}`} x1="0%" y1="0%" x2="100%" y2="0%">
                  <stop offset="0%" stopColor="#0062ea" />
                  <stop offset="32%" stopColor="#027bf3" />
                  <stop offset="68%" stopColor="#00a8b5" />
                  <stop offset="100%" stopColor="#00bca2" />
                </linearGradient>
              </defs>

              {/* H Pillars + Arched Bridge (Scene 3: Activated around pulse) */}
              <g
                style={
                  effectiveReducedMotion
                    ? { opacity: 1 }
                    : {
                        opacity: 0,
                        transformOrigin: "center",
                        animation: `hioRevealH_${uid} ${tHReveal} cubic-bezier(0.16, 1, 0.3, 1) ${dHStart} forwards`,
                      }
                }
              >
                {/* Left Pillar */}
                <rect x="0" y="0" width="70" height="177" rx="35" fill={`url(#hio-grad-${uid})`} />
                {/* Right Pillar */}
                <rect x="106" y="0" width="70" height="177" rx="35" fill={`url(#hio-grad-${uid})`} />
                {/* Arched Bridge */}
                <path
                  d="M 64 68 C 72 53, 104 53, 112 68 L 112 127 C 104 138, 72 138, 64 127 Z"
                  fill={`url(#hio-grad-${uid})`}
                />
              </g>

              {/* Medical Cross (Scene 4: Fades and scales into top-right pillar) */}
              <g
                fill="#ffffff"
                style={
                  effectiveReducedMotion
                    ? { opacity: 1 }
                    : {
                        opacity: 0,
                        transformOrigin: "147px 26.5px",
                        animation: `hioRevealCross_${uid} ${tCross} cubic-bezier(0.34, 1.4, 0.64, 1) ${dCrossStart} forwards`,
                      }
                }
              >
                <rect x="132" y="22" width="30" height="9" rx="4.5" />
                <rect x="142.5" y="11.5" width="9" height="30" rx="4.5" />
              </g>

              {/* Central ECG Heartbeat Pulse Line (Scene 3 & 5: Pulse completion) */}
              <path
                d="M 0 94 L 56 94 L 67 111 L 84 69 L 99 115 L 109 94 L 176 94"
                fill="none"
                stroke="#ffffff"
                strokeWidth="7.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                style={
                  effectiveReducedMotion
                    ? { strokeDashoffset: 0 }
                    : {
                        strokeDasharray: 270,
                        strokeDashoffset: 270,
                        animation: `hioDrawHeartbeat_${uid} ${tHeartbeat} cubic-bezier(0.2, 0.8, 0.2, 1) ${dHStart} forwards`,
                      }
                }
              />
            </svg>
          </div>

          {/* Wordmark: "HealthIO" + Tagline (Scene 6 & 7) */}
          <div
            className="flex flex-col justify-center overflow-hidden"
            style={
              effectiveReducedMotion
                ? { opacity: 1, maxWidth: "420px" }
                : {
                    maxWidth: 0,
                    opacity: 0,
                    animation: `hioExpandTextWrap_${uid} ${tWordmark} cubic-bezier(0.16, 1, 0.3, 1) ${dWordmarkStart} forwards`,
                  }
            }
          >
            <div className="font-bold tracking-tight text-3xl sm:text-4.5xl leading-none flex items-baseline">
              <span className={cn(isDark ? "text-white" : "text-[#0a1936]")}>Health</span>
              <span className="bg-gradient-to-r from-[#009db0] to-[#00bda0] bg-clip-text text-transparent">
                IO
              </span>
            </div>

            {showTagline && (
              <div
                className={cn(
                  "text-[10px] sm:text-[11.5px] font-medium tracking-tight mt-1 sm:mt-1.5 whitespace-nowrap",
                  isDark ? "text-slate-400" : "text-slate-500",
                )}
                style={
                  effectiveReducedMotion
                    ? { opacity: 1 }
                    : {
                        opacity: 0,
                        animation: `hioRevealTagline_${uid} ${tTagline} ease-out ${dTaglineStart} forwards`,
                      }
                }
              >
                Intelligent Healthcare &amp; Medication Management Platform
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Accessible Skip Button (Visible on keyboard focus, or subtle hover) */}
      {canSkip && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            handleSkip();
          }}
          className={cn(
            "absolute top-5 right-5 z-20 text-xs font-semibold px-3 py-1.5 rounded-full border transition-all duration-200",
            "opacity-50 hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-2",
            isDark
              ? "border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800"
              : "border-slate-200 bg-white/70 text-slate-600 hover:bg-white shadow-xs",
          )}
          aria-label="Skip opening animation"
        >
          Skip
        </button>
      )}
    </div>
  );
}

