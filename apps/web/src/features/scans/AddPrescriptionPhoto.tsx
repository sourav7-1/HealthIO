import { Camera, Keyboard, Sparkles } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";

import { Alert, Button, Checkbox, Dialog } from "@/components/ui";
import { useActivePatient } from "@/features/patient/context";
import { errorMessage } from "@/lib/api";
import { bytes } from "@/lib/format";

import { startScanFromPhoto, useAiConsent, useSetAiConsent } from "./api";

const ACCEPT = ["image/jpeg", "image/png", "image/webp"];

/**
 * Add a paper prescription: take or choose a photo, then either let AI read it (only with
 * the patient's permission) or type it in next to the photo. Either way, nothing is saved
 * until a person checks it on the review screen.
 */
export function AddPrescriptionPhoto({ onClose }: { onClose: () => void }) {
  const { patientId, base, mode, can } = useActivePatient();
  const consent = useAiConsent(patientId);
  const setConsent = useSetAiConsent(patientId);
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [allow, setAllow] = useState(false);
  const [busy, setBusy] = useState<"ai" | "manual" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const aiAvailable = consent.data?.ai_available ?? false;
  const granted = consent.data?.granted ?? false;
  const canGrant = can("manage_consent");

  const pick = (f: File | null) => {
    setError(null);
    if (!f) return setFile(null);
    if (!ACCEPT.includes(f.type)) return setError("Use a photo (JPEG, PNG or WebP). PDFs cannot be read yet.");
    if (f.size > 15 * 1024 * 1024) return setError("Photos must be smaller than 15 MB.");
    setFile(f);
  };

  const start = async (how: "ai" | "manual") => {
    if (!file) return setError("Choose a photo first.");
    setBusy(how);
    setError(null);
    try {
      if (how === "ai" && !granted) {
        if (!allow) {
          setBusy(null);
          return setError("Tick the box to allow AI reading, or type it in yourself.");
        }
        await setConsent.mutateAsync(true);
      }
      const scan = await startScanFromPhoto(patientId, file, how);
      onClose();
      void navigate(`${base}/prescriptions/scan/${scan.id}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(null);
    }
  };

  const self = mode === "self";
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add a paper prescription"
      description="Take a clear photo of the whole prescription in good light. Nothing is saved until you check it."
      footer={<Button variant="secondary" onClick={onClose}>Cancel</Button>}
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-line px-4 py-6 text-center hover:bg-surface-2">
          <Camera className="size-6 text-muted" aria-hidden />
          <span className="text-sm font-medium">{file ? file.name : "Take a photo or choose one"}</span>
          <span className="text-xs text-muted">{file ? bytes(file.size) : "JPEG, PNG or WebP, up to 15 MB"}</span>
          <input
            type="file"
            accept={ACCEPT.join(",")}
            capture="environment"
            className="sr-only"
            onChange={(e) => pick(e.target.files?.[0] ?? null)}
          />
        </label>

        {aiAvailable && (granted || canGrant) && (
          <div className="rounded-xl border border-line p-4">
            <p className="flex items-center gap-2 font-medium">
              <Sparkles className="size-4 text-accent" aria-hidden /> Let AI read it for {self ? "you" : "them"}
            </p>
            {!granted && (
              <>
                <p className="mt-2 text-sm text-muted">{consent.data?.notice}</p>
                <div className="mt-3">
                  <Checkbox
                    label={self ? "Allow AI reading of my prescription photos" : "Allow AI reading of their prescription photos"}
                    description="You can turn this off later in Settings."
                    checked={allow}
                    onChange={(e) => setAllow(e.target.checked)}
                  />
                </div>
              </>
            )}
            <Button className="mt-3" disabled={!file} loading={busy === "ai"} onClick={() => void start("ai")}>
              Read it with AI
            </Button>
            <p className="mt-2 text-xs text-muted">
              AI reading is not medical advice and can make mistakes. You will check every field against the photo.
            </p>
          </div>
        )}
        {aiAvailable && !granted && !canGrant && (
          <p className="text-sm text-muted">AI reading needs the patient&apos;s permission. You can type it in instead.</p>
        )}

        <div className="rounded-xl border border-line p-4">
          <p className="flex items-center gap-2 font-medium">
            <Keyboard className="size-4 text-muted" aria-hidden /> Type it in {self ? "yourself" : ""}
          </p>
          <p className="mt-1 text-sm text-muted">The photo is shown next to the form while you type.</p>
          <Button className="mt-3" variant="secondary" disabled={!file} loading={busy === "manual"} onClick={() => void start("manual")}>
            Type it in
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
