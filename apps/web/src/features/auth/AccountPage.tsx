import { HeartPulse } from "lucide-react";

import { Button, Card } from "@/components/ui";

import { useMe, useSession } from "./session";

/** Landing page for roles whose portal is not built yet (patient, caregiver, admin). */
export function AccountPage() {
  const me = useMe();
  const { signOut } = useSession();
  return (
    <main className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center gap-6 px-4">
      <div className="flex items-center gap-2 text-xl font-semibold">
        <HeartPulse className="size-7 text-accent" aria-hidden /> Health Io
      </div>
      <Card title={`Signed in as ${me.display_name}`}>
        <p className="text-sm text-muted">
          The {me.roles.join(" and ")} portal is coming soon. Your account is active and your data is safe.
        </p>
        <Button variant="secondary" className="mt-4" onClick={() => void signOut()}>
          Sign out
        </Button>
      </Card>
    </main>
  );
}
