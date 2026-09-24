/**
 * The prescription as a document, laid out like a printed prescription. Shared by the
 * doctor, patient and caregiver portals. Shows exactly what the doctor wrote; never
 * computes or infers clinical content. Older versions are clearly marked as not current.
 */
import { AlertTriangle, Download, FileClock, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { Alert, Badge, Button, Card, useToast } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { formatDate, formatDateTime, humanize } from "@/lib/format";

import { downloadPrescriptionPdf, usePrescriptionDocument, type PrescriptionDocument } from "./api";
import { QueryState } from "./shared";

const MEAL: Record<string, string | null> = {
  before_food: "Before food",
  after_food: "After food",
  with_food: "With food",
  empty_stomach: "Empty stomach",
  bedtime: "At bedtime",
  any: null,
};

function Banner({ doc, hrefFor }: { doc: PrescriptionDocument; hrefFor: (id: string) => string }) {
  if (doc.status === "issued" || doc.status === "recorded") return null;
  const latest = doc.versions.at(-1);
  return (
    <div className="mb-4">
      <Alert
        tone={doc.status === "draft" ? "warning" : "danger"}
        title={
          doc.status === "superseded"
            ? "This version was corrected and is no longer valid"
            : doc.status === "cancelled"
              ? "This prescription was cancelled"
              : doc.status === "draft"
                ? "Draft: not issued yet"
                : `Not valid (${humanize(doc.status)})`
        }
      >
        {doc.status === "superseded" && latest && latest.id !== doc.id && (
          <Link to={hrefFor(latest.id)} className="font-semibold underline">
            Open the current version (version {latest.revision})
          </Link>
        )}
        {doc.status === "cancelled" && doc.cancel_reason && <p>Reason given by the doctor: {doc.cancel_reason}</p>}
        {doc.status === "draft" && <p>Only the prescribing doctor can see a draft. It is not valid for dispensing.</p>}
      </Alert>
    </div>
  );
}

export function PrescriptionPaper({ doc }: { doc: PrescriptionDocument }) {
  const p = doc.prescriber;
  const pt = doc.patient;
  const current = doc.status === "issued" || doc.status === "recorded";
  return (
    <article
      aria-label="Prescription"
      className={`relative overflow-hidden rounded-2xl border bg-surface p-5 shadow-sm sm:p-8 ${current ? "border-line" : "border-danger/50"}`}
    >
      {!current && (
        <p aria-hidden className="pointer-events-none absolute inset-0 flex select-none items-center justify-center text-6xl font-black uppercase tracking-widest text-danger/10 sm:text-8xl">
          {doc.status === "superseded" ? "Superseded" : doc.status}
        </p>
      )}
      <header className="flex flex-col gap-4 border-b-2 border-fg/80 pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-xl font-semibold">{p?.name ?? "Prescriber not recorded"}</p>
          {p && (p.qualifications.length > 0 || p.specialty) && (
            <p className="text-sm">{[p.qualifications.join(", "), p.specialty].filter(Boolean).join(" · ")}</p>
          )}
          {p?.registration_number && (
            <p className="text-sm">
              Reg. No. {p.registration_number}
              {p.registration_council ? ` (${p.registration_council})` : ""}
            </p>
          )}
          {(p?.practice_name || p?.practice_address) && (
            <p className="text-xs text-muted">{[p.practice_name, p.practice_address].filter(Boolean).join(" · ")}</p>
          )}
        </div>
        <div className="text-sm sm:text-right">
          <p>
            <span className="text-muted">Date </span>
            <span className="font-medium">{formatDate(doc.prescribed_on)}</span>
          </p>
          <p className="text-muted">Version {doc.revision}</p>
        </div>
      </header>

      <section className="border-b border-line py-3 text-sm">
        <span className="text-muted">Patient: </span>
        {pt ? (
          <span className="font-medium">
            {[pt.name, pt.age_years != null ? `${pt.age_years} y` : null, pt.sex && pt.sex !== "unknown" ? humanize(pt.sex) : null]
              .filter(Boolean)
              .join(" · ")}
          </span>
        ) : (
          <span className="italic text-muted">details not shared with you</span>
        )}
      </section>

      {doc.diagnosis_as_written && (
        <section className="py-3 text-sm">
          <span className="text-muted">Diagnosis / assessment (as documented): </span>
          <span className="font-medium">{doc.diagnosis_as_written}</span>
        </section>
      )}

      <section className="pt-2">
        <p aria-hidden className="font-serif text-3xl font-bold italic">℞</p>
        <ol className="mt-2 divide-y divide-line">
          {doc.items.map((i) => {
            const how = [i.dose, i.frequency, i.meal_relation ? MEAL[i.meal_relation] ?? humanize(i.meal_relation) : null, i.route]
              .filter(Boolean)
              .join(" · ");
            return (
              <li key={i.sequence} className="grid gap-1 py-3 sm:grid-cols-[2rem_1fr_1fr_6rem] sm:gap-3">
                <span className="text-sm text-muted">{i.sequence}.</span>
                <div>
                  <p className="font-semibold">
                    {i.medicine}
                    {i.strength && <span className="font-normal"> {i.strength}</span>}
                    {i.dosage_form && <span className="font-normal text-muted"> ({i.dosage_form})</span>}
                  </p>
                  {i.generic_name && <p className="text-xs text-muted">Generic: {i.generic_name}</p>}
                </div>
                <div className="text-sm">
                  <p>{how || (i.is_prn ? "" : "Directions not recorded")}</p>
                  {i.is_prn && <p>When needed{i.prn_reason ? `: ${i.prn_reason}` : ""}</p>}
                  {i.instructions && <p className="italic text-muted">{i.instructions}</p>}
                </div>
                <p className="text-sm sm:text-right">
                  {i.duration_days ? `${i.duration_days} days` : i.is_prn ? "As needed" : "—"}
                </p>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="flex flex-col gap-2 border-t border-line pt-3 text-sm">
        {doc.advice && (
          <p>
            <span className="text-muted">Notes and advice: </span>
            <span className="whitespace-pre-line">{doc.advice}</span>
          </p>
        )}
        {doc.follow_up_on && (
          <p>
            <span className="text-muted">Follow-up: </span>
            <span className="font-medium">on or before {formatDate(doc.follow_up_on)}</span>
            {doc.follow_up_instructions ? ` · ${doc.follow_up_instructions}` : ""}
          </p>
        )}
        {doc.valid_until && <p className="text-muted">Valid until {formatDate(doc.valid_until)}</p>}
        {doc.revision > 1 && (
          <p>
            <span className="text-muted">Corrected prescription (version {doc.revision}). Reason: </span>
            {doc.revision_reason}
          </p>
        )}
      </section>

      {doc.provenance && (
        <p className="mt-4 rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted" role="note">
          {doc.provenance}
        </p>
      )}

      <footer className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        {doc.content_sha256 ? (
          <p className="flex items-center gap-1 text-xs text-muted" title={`SHA-256 ${doc.content_sha256}`}>
            <ShieldCheck className="size-3.5" aria-hidden /> Fingerprint {doc.content_sha256.slice(0, 16)}…
          </p>
        ) : (
          <span />
        )}
        <div className="text-sm sm:text-right">
          <p className="text-muted">
            {doc.issued_at
              ? `Electronically issued ${formatDateTime(doc.issued_at)}`
              : doc.source === "doctor_issued"
                ? "Not issued"
                : "Paper prescription"}
          </p>
          {p && <p className="font-semibold">{p.name}</p>}
        </div>
      </footer>
    </article>
  );
}

function History({ doc, hrefFor }: { doc: PrescriptionDocument; hrefFor: (id: string) => string }) {
  if (doc.versions.length <= 1) return null;
  return (
    <Card title="Versions" className="mt-6">
      <p className="mb-3 text-sm text-muted">
        Corrections never change an earlier version. Each correction is a new version, and every version stays in the record.
      </p>
      <ol className="flex flex-col gap-2">
        {[...doc.versions].reverse().map((v) => (
          <li key={v.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line px-3 py-2 text-sm">
            <div>
              <p className="font-medium">
                Version {v.revision}
                {v.id === doc.id && <span className="font-normal text-muted"> (shown above)</span>}
              </p>
              <p className="text-muted">
                {v.issued_at ? `Issued ${formatDateTime(v.issued_at)}` : "Not issued"}
                {v.revision_reason ? ` · ${v.revision_reason}` : ""}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone={v.status === "issued" ? "success" : v.status === "draft" ? "warning" : "neutral"}>
                {v.status === "issued" ? "Current" : humanize(v.status)}
              </Badge>
              {v.id !== doc.id && (
                <Link to={hrefFor(v.id)} className="font-medium text-accent underline">
                  View
                </Link>
              )}
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

export function PrescriptionView({
  patientId,
  prescriptionId,
  hrefFor,
}: {
  patientId: string;
  prescriptionId: string;
  /** Link to another version of this prescription in the current portal. */
  hrefFor: (id: string) => string;
}) {
  const document = usePrescriptionDocument(patientId, prescriptionId);
  const toast = useToast();
  const [downloading, setDownloading] = useState(false);

  const download = async (doc: PrescriptionDocument) => {
    setDownloading(true);
    try {
      await downloadPrescriptionPdf(patientId, doc.id, `prescription-${doc.prescribed_on ?? "undated"}-v${doc.revision}.pdf`);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <QueryState query={document} what="Prescription">
      {(doc) => (
        <div className="mx-auto max-w-3xl">
          <Banner doc={doc} hrefFor={hrefFor} />
          <div className="mb-4 flex flex-wrap items-center justify-end gap-2">
            {doc.versions.length > 1 && (
              <span className="mr-auto inline-flex items-center gap-1 text-sm text-muted">
                <FileClock className="size-4" aria-hidden /> {doc.versions.length} versions
              </span>
            )}
            <Button variant="secondary" icon={<Download className="size-4" />} loading={downloading} onClick={() => void download(doc)}>
              Download PDF
            </Button>
          </div>
          <PrescriptionPaper doc={doc} />
          {doc.status !== "issued" && doc.status !== "recorded" && doc.status !== "draft" && (
            <p className="mt-3 flex items-center gap-2 text-sm text-danger">
              <AlertTriangle className="size-4" aria-hidden /> Do not use this version to buy or take medicines.
            </p>
          )}
          <History doc={doc} hrefFor={hrefFor} />
        </div>
      )}
    </QueryState>
  );
}
