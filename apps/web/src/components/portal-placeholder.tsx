import Link from "next/link";

export function PortalPlaceholder({ title, phase }: { title: string; phase: number }) {
  return (
    <main className="mx-auto max-w-3xl px-4 py-16">
      <h1 className="text-2xl font-semibold">{title}</h1>
      <p className="mt-2 text-[var(--muted)]">This portal is built in phase {phase}.</p>
      <Link href="/" className="mt-6 inline-block text-brand-600 underline">
        Back to start
      </Link>
    </main>
  );
}
