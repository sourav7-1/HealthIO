import Link from "next/link";

import { portals } from "@/lib/portals";

export default function HomePage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-16">
      <h1 className="text-3xl font-semibold tracking-tight">Health Io</h1>
      <p className="mt-2 text-[var(--muted)]">
        Prescriptions, medicines, reminders and records in one place.
      </p>
      <ul className="mt-10 grid gap-3 sm:grid-cols-2">
        {portals.map((portal) => (
          <li key={portal.href}>
            <Link
              href={portal.href}
              className="block min-h-11 rounded-lg border border-[var(--border)] bg-[var(--surface)] p-5 transition-colors hover:border-brand-500 focus-visible:outline-2 focus-visible:outline-brand-500"
            >
              <span className="font-medium">{portal.title}</span>
              <span className="mt-1 block text-sm text-[var(--muted)]">{portal.description}</span>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
