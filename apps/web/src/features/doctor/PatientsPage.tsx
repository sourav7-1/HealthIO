import { Link2, Search, UserPlus, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Badge, Button, Card, EmptyState, ErrorState, Input, SkeletonList } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { ageFrom, formatDate, humanize, initials } from "@/lib/format";

import { usePatients } from "./api";
import { AddPatientDialog } from "./forms/AddPatientDialog";
import { ConnectPatientDialog } from "./forms/ConnectPatientDialog";
import { PatientName } from "./shared";

function useDebounced(value: string, ms = 300): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export function PatientsPage() {
  const [query, setQuery] = useState("");
  const q = useDebounced(query.trim());
  const patients = usePatients(q);
  const [dialog, setDialog] = useState<"add" | "connect" | null>(null);

  return (
    <>
      <PageHeader
        title="Patients"
        description="Only patients who are connected to you appear here."
        actions={
          <>
            <Button variant="secondary" icon={<Link2 className="size-4" />} onClick={() => setDialog("connect")}>
              Connect existing
            </Button>
            <Button icon={<UserPlus className="size-4" />} onClick={() => setDialog("add")}>
              Add patient
            </Button>
          </>
        }
      />

      <div className="relative mb-4">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" aria-hidden />
        <Input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name"
          aria-label="Search patients by name"
          className="pl-9"
        />
      </div>

      <Card bodyClassName="p-0">
        {patients.isPending ? (
          <div className="p-4">
            <SkeletonList rows={5} />
          </div>
        ) : patients.isError ? (
          <ErrorState message={errorMessage(patients.error)} onRetry={() => void patients.refetch()} />
        ) : patients.data.length === 0 ? (
          q ? (
            <EmptyState
              icon={<Search className="size-5" />}
              title={`No patients match “${q}”`}
              description="Search covers only patients who share their name with you."
            />
          ) : (
            <EmptyState
              icon={<Users className="size-5" />}
              title="No patients yet"
              description="Add a patient you are seeing in person, or send a connection request to a patient who already uses Health Io."
              action={
                <Button variant="secondary" onClick={() => setDialog("add")}>
                  Add a patient
                </Button>
              }
            />
          )
        ) : (
          <>
            {/* Table on larger screens */}
            <table className="hidden w-full text-sm md:table">
              <thead className="border-b border-line text-left text-muted">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">Patient</th>
                  <th scope="col" className="px-4 py-3 font-medium">Age / sex</th>
                  <th scope="col" className="px-4 py-3 font-medium">Connected since</th>
                  <th scope="col" className="px-4 py-3 font-medium">Shared with you</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {patients.data.map((p) => (
                  <tr key={p.patient_id} className="hover:bg-surface-2">
                    <td className="px-4 py-3">
                      <Link to={`/doctor/patients/${p.patient_id}`} className="flex items-center gap-3 font-medium hover:underline">
                        <span aria-hidden className="flex size-8 items-center justify-center rounded-full bg-surface-2 text-xs font-semibold">
                          {initials(p.display_name)}
                        </span>
                        <PatientName name={p.display_name} />
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-muted">
                      {ageFrom(p.date_of_birth) ?? "—"}
                      {p.sex_at_birth && p.sex_at_birth !== "unknown" ? ` · ${humanize(p.sex_at_birth)}` : ""}
                    </td>
                    <td className="px-4 py-3 text-muted">{formatDate(p.since)}</td>
                    <td className="px-4 py-3">
                      <Badge tone="neutral">{p.shared_categories.length} of 10 categories</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {/* Cards on phones */}
            <ul className="divide-y divide-line md:hidden">
              {patients.data.map((p) => (
                <li key={p.patient_id}>
                  <Link to={`/doctor/patients/${p.patient_id}`} className="flex items-center gap-3 px-4 py-3">
                    <span aria-hidden className="flex size-10 shrink-0 items-center justify-center rounded-full bg-surface-2 text-sm font-semibold">
                      {initials(p.display_name)}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate font-medium">
                        <PatientName name={p.display_name} />
                      </p>
                      <p className="text-sm text-muted">
                        {ageFrom(p.date_of_birth) !== null ? `${ageFrom(p.date_of_birth)} y · ` : ""}
                        since {formatDate(p.since)}
                      </p>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>

      <AddPatientDialog open={dialog === "add"} onClose={() => setDialog(null)} />
      <ConnectPatientDialog open={dialog === "connect"} onClose={() => setDialog(null)} />
    </>
  );
}
