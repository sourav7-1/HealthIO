import type { SVGProps } from "react";
import { cn } from "./primitives";

export interface LogoIconProps extends SVGProps<SVGSVGElement> {
  className?: string;
}

/**
 * HealthIO vector mark (H-pill with heartbeat waveform, medical cross, and blue-to-teal gradient).
 */
export function LogoIcon({ className, ...props }: LogoIconProps) {
  return (
    <svg
      viewBox="0 0 176 177"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={cn("shrink-0", className)}
      aria-hidden="true"
      {...props}
    >
      <defs>
        <linearGradient id="hio-logo-gradient" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#0062ea" />
          <stop offset="32%" stopColor="#027bf3" />
          <stop offset="68%" stopColor="#00a8b5" />
          <stop offset="100%" stopColor="#00bca2" />
        </linearGradient>
      </defs>
      {/* Left Pillar */}
      <rect x="0" y="0" width="70" height="177" rx="35" fill="url(#hio-logo-gradient)" />
      {/* Right Pillar */}
      <rect x="106" y="0" width="70" height="177" rx="35" fill="url(#hio-logo-gradient)" />
      {/* Center Arched Bridge */}
      <path
        d="M 64 68 C 72 53, 104 53, 112 68 L 112 127 C 104 138, 72 138, 64 127 Z"
        fill="url(#hio-logo-gradient)"
      />
      {/* Plus Symbol in Top-Right Pillar */}
      <rect x="132" y="22" width="30" height="9" rx="4.5" fill="#ffffff" />
      <rect x="142.5" y="11.5" width="9" height="30" rx="4.5" fill="#ffffff" />
      {/* ECG Heartbeat Pulse Line */}
      <path
        d="M 0 94 L 56 94 L 67 111 L 84 69 L 99 115 L 109 94 L 176 94"
        fill="none"
        stroke="#ffffff"
        strokeWidth="7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export interface LogoProps {
  variant?: "full" | "mark" | "image";
  size?: "sm" | "md" | "lg" | "xl";
  showTagline?: boolean;
  className?: string;
  imageClassName?: string;
}

const SIZES = {
  sm: {
    icon: "size-6",
    text: "text-lg",
    image: "h-6",
    tagline: "text-[9px] -mt-0.5",
  },
  md: {
    icon: "size-7",
    text: "text-xl",
    image: "h-8",
    tagline: "text-[10px] -mt-0.5",
  },
  lg: {
    icon: "size-9",
    text: "text-2xl",
    image: "h-11",
    tagline: "text-xs mt-0.5",
  },
  xl: {
    icon: "size-12",
    text: "text-3xl",
    image: "h-14",
    tagline: "text-xs mt-1",
  },
};

/**
 * Official HealthIO Brand Logo component.
 * Supports vector marks, styled text that adapts to theme/dark-mode, and full original logo images.
 */
export function Logo({
  variant = "full",
  size = "md",
  showTagline = false,
  className,
  imageClassName,
}: LogoProps) {
  const currentSize = SIZES[size];

  if (variant === "image") {
    return (
      <div className={cn("inline-flex flex-col items-start", className)}>
        <img
          src="/logo.png"
          alt="HealthIO"
          className={cn("w-auto object-contain", currentSize.image, imageClassName)}
        />
      </div>
    );
  }

  if (variant === "mark") {
    return <LogoIcon className={cn(currentSize.icon, className)} />;
  }

  return (
    <div className={cn("inline-flex items-center gap-2 select-none", className)}>
      <LogoIcon className={currentSize.icon} />
      <div className="flex flex-col justify-center leading-none">
        <span className={cn("font-bold tracking-tight", currentSize.text)}>
          <span className="text-fg">Health</span>
          <span className="bg-gradient-to-r from-[#009db0] to-[#00bda0] bg-clip-text text-transparent">
            IO
          </span>
        </span>
        {showTagline && (
          <span className={cn("font-normal text-muted tracking-tight", currentSize.tagline)}>
            Intelligent Healthcare & Medication Management Platform
          </span>
        )}
      </div>
    </div>
  );
}

