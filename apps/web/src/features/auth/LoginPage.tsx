import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useLocation } from "react-router";
import { z } from "zod";

import { Alert, Button, Field, Input, Logo } from "@/components/ui";
import { errorMessage } from "@/lib/api";

import { homeFor } from "./guards";
import { useSession } from "./session";

const schema = z.object({
  email: z.email("Enter a valid email address"),
  password: z.string().min(1, "Enter your password"),
});
type Values = z.infer<typeof schema>;

export function LoginPage() {
  const { state, signIn } = useSession();
  const location = useLocation();
  const [error, setError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema) });

  if (state.status === "signed-in") {
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from ?? homeFor(state.me)} replace />;
  }

  const onSubmit = handleSubmit(async (values) => {
    setError(null);
    try {
      await signIn(values.email, values.password);
    } catch (err) {
      setError(errorMessage(err));
    }
  });

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Logo size="lg" showTagline />
        </div>
        <div className="rounded-2xl border border-line bg-surface p-6">
          <h1 className="text-lg font-semibold">Sign in</h1>
          <p className="mt-1 text-sm text-muted">Use the email and password for your account.</p>
          <form onSubmit={onSubmit} className="mt-6 flex flex-col gap-4" noValidate>
            {error && <Alert tone="danger">{error}</Alert>}
            <Field label="Email" error={errors.email?.message}>
              {(p) => <Input {...p} type="email" autoComplete="username" {...register("email")} />}
            </Field>
            <Field label="Password" error={errors.password?.message}>
              {(p) => (
                <Input {...p} type="password" autoComplete="current-password" {...register("password")} />
              )}
            </Field>
            <Button type="submit" loading={isSubmitting} className="mt-2 w-full">
              Sign in
            </Button>
          </form>
        </div>
        <p className="mt-6 text-center text-xs text-muted">
          Health Io records and organises health information. It does not replace your doctor.
        </p>
        <div className="mt-3 flex justify-center">
          <Link
            to="/splash"
            className="text-xs text-muted hover:text-accent underline underline-offset-4 transition-colors"
          >
            Experience HealthIO opening animation
          </Link>
        </div>
      </div>
    </main>
  );
}
