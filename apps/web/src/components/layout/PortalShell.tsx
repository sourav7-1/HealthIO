import { HeartPulse, LogOut, Menu, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink } from "react-router";

import { cn } from "@/components/ui";
import { useMe, useSession } from "@/features/auth/session";
import { initials } from "@/lib/format";

export interface NavItem {
  to: string;
  label: string;
  icon: ReactNode;
  end?: boolean;
}

export function PortalShell({
  portalName,
  nav,
  children,
}: {
  portalName: string;
  nav: NavItem[];
  children: ReactNode;
}) {
  const me = useMe();
  const { signOut } = useSession();
  const [open, setOpen] = useState(false);

  const navList = (
    <ul className="flex flex-col gap-1">
      {nav.map((item) => (
        <li key={item.to}>
          <NavLink
            to={item.to}
            end={item.end}
            onClick={() => setOpen(false)}
            className={({ isActive }) =>
              cn(
                "flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium",
                isActive ? "bg-brand-50 text-brand-700 dark:bg-brand-700/25 dark:text-brand-100" : "text-muted hover:bg-surface-2 hover:text-fg",
              )
            }
          >
            {item.icon}
            {item.label}
          </NavLink>
        </li>
      ))}
    </ul>
  );

  const account = (
    <div className="flex items-center gap-3 border-t border-line pt-4">
      <div
        aria-hidden
        className="flex size-9 shrink-0 items-center justify-center rounded-full bg-surface-2 text-sm font-semibold"
      >
        {initials(me.display_name)}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{me.display_name}</p>
        <p className="truncate text-xs text-muted">{portalName}</p>
      </div>
      <button
        type="button"
        onClick={() => void signOut()}
        className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-fg"
        aria-label="Sign out"
        title="Sign out"
      >
        <LogOut className="size-5" />
      </button>
    </div>
  );

  const brand = (
    <div className="flex items-center gap-2 px-1 font-semibold">
      <HeartPulse className="size-6 text-accent" aria-hidden />
      <span>Health Io</span>
    </div>
  );

  return (
    <div className="min-h-dvh md:grid md:grid-cols-[16rem_1fr]">
      <a
        href="#main"
        className="sr-only z-50 rounded bg-surface px-3 py-2 focus:not-sr-only focus:fixed focus:left-2 focus:top-2"
      >
        Skip to content
      </a>

      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh flex-col gap-6 border-r border-line bg-surface p-4 md:flex">
        {brand}
        <nav aria-label={`${portalName} navigation`} className="flex-1">
          {navList}
        </nav>
        {account}
      </aside>

      {/* Mobile top bar + drawer */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-line bg-surface px-4 py-2 md:hidden">
        {brand}
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-lg p-2 hover:bg-surface-2"
          aria-label="Open menu"
          aria-expanded={open}
        >
          <Menu className="size-6" />
        </button>
      </header>
      {open && (
        <div className="fixed inset-0 z-40 md:hidden" role="dialog" aria-modal="true" aria-label="Menu">
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            aria-label="Close menu"
            onClick={() => setOpen(false)}
          />
          <div className="absolute inset-y-0 left-0 flex w-72 max-w-[85%] flex-col gap-6 bg-surface p-4">
            <div className="flex items-center justify-between">
              {brand}
              <button type="button" onClick={() => setOpen(false)} className="rounded-lg p-2 hover:bg-surface-2" aria-label="Close menu">
                <X className="size-5" />
              </button>
            </div>
            <nav aria-label={`${portalName} navigation`} className="flex-1">
              {navList}
            </nav>
            {account}
          </div>
        </div>
      )}

      <main id="main" className="min-w-0 px-4 py-6 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  back,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  back?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {back}
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <div className="mt-1 text-sm text-muted">{description}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
