import { useState } from "react";
import { Link } from "react-router";
import { ArrowLeft, Moon, Play, RotateCcw, Smartphone, Sun, Zap } from "lucide-react";

import { Button, Card, HealthIOSplash } from "@/components/ui";

export function SplashPreviewPage() {
  const [key, setKey] = useState(0);
  const [fullscreen, setFullscreen] = useState(false);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [duration, setDuration] = useState(2200);
  const [reducedMotion, setReducedMotion] = useState(false);
  const [showTagline, setShowTagline] = useState(true);
  const [viewport, setViewport] = useState<"desktop" | "mobile">("desktop");
  const [completedCount, setCompletedCount] = useState(0);

  const handleReplay = () => {
    setKey((k) => k + 1);
  };

  const handleFullscreenPlay = () => {
    setFullscreen(true);
    setKey((k) => k + 1);
  };

  return (
    <div className="min-h-screen bg-bg text-fg p-4 sm:p-8 flex flex-col items-center">
      {/* Top Header */}
      <div className="w-full max-w-4xl flex items-center justify-between pb-6 border-b border-line mb-6">
        <Link
          to="/login"
          className="inline-flex items-center gap-2 text-sm font-semibold text-muted hover:text-fg transition-colors"
        >
          <ArrowLeft className="size-4" /> Back to App
        </Link>
        <div className="flex items-center gap-3">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted">
            HealthIO Brand Studio
          </span>
        </div>
      </div>

      <div className="w-full max-w-4xl grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Controls Column */}
        <div className="lg:col-span-1 flex flex-col gap-4">
          <Card title="Opening Animation Controls">
            <div className="flex flex-col gap-4 text-sm">
              <div>
                <label className="font-medium text-xs text-muted block mb-1.5">Theme Mode</label>
                <div className="grid grid-cols-2 gap-2">
                  <Button
                    variant={theme === "light" ? "primary" : "secondary"}
                    size="sm"
                    onClick={() => setTheme("light")}
                  >
                    <Sun className="size-3.5 mr-1.5" /> Light
                  </Button>
                  <Button
                    variant={theme === "dark" ? "primary" : "secondary"}
                    size="sm"
                    onClick={() => setTheme("dark")}
                  >
                    <Moon className="size-3.5 mr-1.5" /> Dark
                  </Button>
                </div>
              </div>

              <div>
                <label className="font-medium text-xs text-muted block mb-1.5">
                  Duration ({duration}ms)
                </label>
                <input
                  type="range"
                  min="1800"
                  max="2800"
                  step="100"
                  value={duration}
                  onChange={(e) => {
                    setDuration(Number(e.target.value));
                    handleReplay();
                  }}
                  className="w-full accent-accent cursor-pointer"
                />
                <div className="flex justify-between text-[11px] text-muted mt-1">
                  <span>1.8s (Fast)</span>
                  <span>2.2s (Standard)</span>
                  <span>2.8s (Slow)</span>
                </div>
              </div>

              <div>
                <label className="font-medium text-xs text-muted block mb-1.5">Viewport Simulation</label>
                <div className="grid grid-cols-2 gap-2">
                  <Button
                    variant={viewport === "desktop" ? "primary" : "secondary"}
                    size="sm"
                    onClick={() => setViewport("desktop")}
                  >
                    Desktop
                  </Button>
                  <Button
                    variant={viewport === "mobile" ? "primary" : "secondary"}
                    size="sm"
                    onClick={() => setViewport("mobile")}
                  >
                    <Smartphone className="size-3.5 mr-1.5" /> Mobile
                  </Button>
                </div>
              </div>

              <div className="flex flex-col gap-2 pt-2 border-t border-line">
                <label className="flex items-center gap-2 cursor-pointer text-xs font-medium">
                  <input
                    type="checkbox"
                    checked={reducedMotion}
                    onChange={(e) => {
                      setReducedMotion(e.target.checked);
                      handleReplay();
                    }}
                    className="accent-accent rounded"
                  />
                  <span>Prefers Reduced Motion</span>
                </label>

                <label className="flex items-center gap-2 cursor-pointer text-xs font-medium">
                  <input
                    type="checkbox"
                    checked={showTagline}
                    onChange={(e) => {
                      setShowTagline(e.target.checked);
                      handleReplay();
                    }}
                    className="accent-accent rounded"
                  />
                  <span>Include Tagline</span>
                </label>
              </div>

              <div className="pt-2 flex flex-col gap-2">
                <Button variant="primary" onClick={handleReplay} className="w-full">
                  <RotateCcw className="size-4 mr-2" /> Replay In Window
                </Button>
                <Button variant="secondary" onClick={handleFullscreenPlay} className="w-full">
                  <Play className="size-4 mr-2" /> Test Fullscreen Modal
                </Button>
              </div>

              {completedCount > 0 && (
                <div className="text-[11px] text-muted text-center pt-2">
                  Completed cycles: {completedCount}
                </div>
              )}
            </div>
          </Card>

          <div className="p-4 rounded-xl border border-line bg-surface-2 text-xs text-muted leading-relaxed">
            <strong className="text-fg block mb-1">Story Timeline (8 Scenes):</strong>
            <ul className="list-disc pl-4 space-y-1">
              <li>1. Initial quiet background (0.2s)</li>
              <li>2. Digital ECG pulse stroke from left</li>
              <li>3. Flows into &amp; activates H-icon</li>
              <li>4. Subtle medical cross reveal</li>
              <li>5. Heartbeat wave settles cleanly</li>
              <li>6. HealthIO wordmark glide &amp; fade</li>
              <li>7. Full brand lockup &amp; tagline</li>
              <li>8. Seamless application transition</li>
            </ul>
          </div>
        </div>

        {/* Preview Frame Column */}
        <div className="lg:col-span-2 flex flex-col items-center justify-center">
          <div
            className={`relative rounded-2xl border border-line overflow-hidden shadow-xl transition-all duration-300 ${
              viewport === "mobile" ? "w-[360px] h-[640px]" : "w-full h-[520px]"
            }`}
          >
            <HealthIOSplash
              key={key}
              theme={theme}
              duration={duration}
              reducedMotion={reducedMotion}
              showTagline={showTagline}
              canSkip={true}
              onComplete={() => {
                setCompletedCount((c) => c + 1);
              }}
              className="absolute"
            />

            {/* Post-transition simulated application screen */}
            <div className="w-full h-full p-8 flex flex-col items-center justify-center bg-surface text-center">
              <div className="size-12 rounded-full bg-emerald-500/10 text-emerald-600 flex items-center justify-center mb-4">
                <Zap className="size-6" />
              </div>
              <h2 className="text-lg font-bold">HealthIO Application Ready</h2>
              <p className="text-sm text-muted max-w-sm mt-1 mb-4">
                The opening animation completed and transitioned seamlessly into your clinical dashboard.
              </p>
              <Button size="sm" variant="secondary" onClick={handleReplay}>
                Play Again
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Fullscreen Modal Test */}
      {fullscreen && (
        <HealthIOSplash
          key={`fs-${key}`}
          theme={theme}
          duration={duration}
          reducedMotion={reducedMotion}
          showTagline={showTagline}
          canSkip={true}
          onComplete={() => {
            setFullscreen(false);
            setCompletedCount((c) => c + 1);
          }}
        />
      )}
    </div>
  );
}

