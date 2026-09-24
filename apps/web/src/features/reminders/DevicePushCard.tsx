import { useEffect, useState } from "react";

import { Alert, Button, Card, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { disablePush, enablePush, pushState, type PushState } from "@/lib/push";

const EXPLAIN: Record<PushState, string> = {
  unsupported: "This browser can’t show notifications. Reminders still appear while Health Io is open.",
  "not-configured": "Notifications aren’t set up on this server yet. Reminders still appear while Health Io is open.",
  denied: "Notifications are blocked for Health Io in this browser’s site settings. Allow them there, then come back.",
  off: "Get reminders on this device even when Health Io is closed. On iPhone, add Health Io to your Home Screen first.",
  on: "This device gets medicine reminders. Turn off on shared devices.",
};

/** Web Push for this browser or installed app (one subscription per device). */
export function DevicePushCard() {
  const [state, setState] = useState<PushState | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  useEffect(() => {
    let live = true;
    pushState()
      .then((s) => live && setState(s))
      .catch(() => live && setState("unsupported"));
    return () => {
      live = false;
    };
  }, []);

  const toggle = async (on: boolean) => {
    setBusy(true);
    try {
      const next = on ? await enablePush() : await disablePush();
      setState(next);
      if (next === "on") toast.success("Notifications turned on for this device");
      else if (!on) toast.success("Notifications turned off for this device");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Notifications on this device">
      {state === null ? (
        <p className="text-sm text-muted">Checking this device…</p>
      ) : (
        <div className="flex flex-col gap-3">
          {state === "denied" ? <Alert tone="warning">{EXPLAIN.denied}</Alert> : <p className="text-sm text-muted">{EXPLAIN[state]}</p>}
          {state === "off" && (
            <div>
              <Button onClick={() => void toggle(true)} loading={busy}>
                Turn on notifications
              </Button>
            </div>
          )}
          {state === "on" && (
            <div>
              <Button variant="secondary" onClick={() => void toggle(false)} loading={busy}>
                Turn off on this device
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
