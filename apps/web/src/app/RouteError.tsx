import { Link, useRouteError } from "react-router";

import { EmptyState } from "@/components/ui";

export function RouteError({ notFound }: { notFound?: boolean }) {
  const error = useRouteError();
  if (error) console.error(error); // details stay in the console, never on screen
  return (
    <main className="flex min-h-dvh items-center justify-center">
      <EmptyState
        title={notFound ? "Page not found" : "Something went wrong"}
        description={notFound ? "The page you asked for does not exist." : "Please reload the page. If this keeps happening, contact support."}
        action={
          <Link to="/" className="text-sm font-medium text-accent hover:underline">
            Go to the start page
          </Link>
        }
      />
    </main>
  );
}
