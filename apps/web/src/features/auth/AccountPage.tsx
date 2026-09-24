import { Button, Card, Logo } from "@/components/ui";

import { useMe, useSession } from "./session";

/** Landing page for roles whose portal is not built yet (patient, caregiver, admin). */
export function AccountPage() {
  const me = useMe();
  const { signOut } = useSession();
  return (
    <main className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center gap-6 px-4">
      <div>
        <Logo size="lg" />
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
