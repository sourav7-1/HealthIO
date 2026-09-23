# ADR 0002: React + Vite SPA for the frontend; PWA instead of a native app

- **Status:** accepted
- **Date:** 2026-09-24
- **Supersedes:** the web and mobile rows of [ADR 0001](0001-stack.md)

## Context
The platform specification (2026-09-24) sets the frontend stack as **React, TypeScript, Vite and Tailwind CSS**, and lists no native mobile app. Phase 0 had scaffolded a minimal Next.js 16 skeleton (placeholder pages only) and planned a separate Expo app.

## Decision
- `apps/web` becomes a **React 19 + Vite SPA** that contains all four portals (lazy-loaded route trees). The Next.js skeleton is replaced in roadmap Phase 1b. Nothing beyond placeholders is lost; `packages/api-client`, `packages/ui-tokens` and `packages/config` carry over unchanged.
- There is **no native mobile app** for now. The SPA is an installable **PWA** with Web Push for reminders.
- In production, the SPA is served by nginx on the **same origin** as the API (`/` and `/api`). This removes CORS and lets the refresh cookie be `SameSite=Strict`.

## Consequences
- There is no server-side rendering. That is acceptable: every portal sits behind a login, and SEO matters only for a small public landing page, which can be static.
- Web Push on iOS works only for PWAs added to the home screen (iOS 16.4+). Reminders on iOS therefore depend on installation; SMS escalation covers this gap. If reminder reliability on iOS proves insufficient in the pilot, a React Native app can reuse `packages/api-client` and `packages/ui-tokens`.
- The route groups and portal list from the Next.js skeleton map directly onto `src/routes/{public,patient,caregiver,doctor,admin}`.
