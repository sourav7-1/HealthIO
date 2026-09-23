import { useState } from "react";
import type { FieldValues, Path, UseFormSetError } from "react-hook-form";

import { useToast } from "@/components/ui";
import { ApiError, errorMessage } from "@/lib/api";

/**
 * Wraps a form submit: shows server validation errors on the matching fields, a banner
 * for everything else, and a success toast.
 */
export function useSubmit<T extends FieldValues>(setError: UseFormSetError<T>) {
  const toast = useToast();
  const [formError, setFormError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>, success: string): Promise<boolean> {
    setFormError(null);
    try {
      await action();
      toast.success(success);
      return true;
    } catch (err) {
      if (err instanceof ApiError) {
        const fields = err.fieldErrors();
        Object.entries(fields).forEach(([name, msg]) =>
          setError(name as Path<T>, { type: "server", message: msg }),
        );
        if (Object.keys(fields).length > 0 && err.status === 422) {
          setFormError("Please check the highlighted fields.");
          return false;
        }
      }
      setFormError(errorMessage(err));
      return false;
    }
  }

  return { run, formError, clearFormError: () => setFormError(null) };
}
