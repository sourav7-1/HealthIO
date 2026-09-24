/** Caregiver portal: everyone I look after at a glance, invitations, and adding dependants. */
import { AlertTriangle, CalendarDays, FileText, HeartHandshake, Lock, Pill, UserPlus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Badge, Button, Card, Checkbox, Dialog, EmptyState, Field, Input, Select, SkeletonList, ErrorState, useToast } from "@/components/ui";
import { StatusBadge } from "@/features/chart/shared";
import { TODO_DOSE } from "@/features/patient/api";
import { DoseCard } from "@/features/patient/components";
import { useActivePatient } from "@/features/patient/context";
import { ReminderCard } from "@/features/patient/pages/ProfilePages";
import { DevicePushCard } from "@/features/reminders/DevicePushCard";
import { DEPENDANT_BASES, basisLabel, scopeLabel } from "@/features/patient/scopes";
import { errorMessage, type Schemas } from "@/lib/api";
import { formatDate, formatDateTime, formatTime, humanize, todayIso } from "@/lib/format";

import { useCareDashboard, useCaregiving, useCreateDependant, useLeave, useRespondToInvitation, type CareLink, type PersonSummary } from "./api";

// --- Dashboard ---------------------------------------------------------------------------------

export function CareDashboardPage() {
  const dashboard = useCareDashboard();
  const links = useCaregiving();
  const [adding, setAdding] = useState(false);
  const invitations = (links.data ?? []).filter((l) => l.status === "invited");

  return (
    <>
      <PageHeader
        title="People you care for"
        description="Today at a glance for everyone you help. You only see what each person (or their guardian) has shared with you."
        actions={<Button icon={<UserPlus className="size-4" />} onClick={() => setAdding(true)}>Add someone you look after</Button>}
      />
      {invitations.length > 0 && (
        <div className="mb-6 flex flex-col gap-3">
          {invitations.map((inv) => <InvitationCard key={inv.relationship_id} invitation={inv} />)}
        </div>
      )}
      {dashboard.isPending ? (
        <SkeletonList rows={2} />
      ) : dashboard.isError ? (
        <ErrorState message={errorMessage(dashboard.error)} onRetry={() => void dashboard.refetch()} />
      ) : dashboard.data.people.length === 0 ? (
        <Card>
          <EmptyState
            icon={<HeartHandshake className="size-5" />}
            title="You are not caring for anyone yet"
            description="If the person uses Health Io, ask them to invite you from their Caregivers page. For a child, or an adult you are authorised to represent, add them yourself."
            action={<Button variant="secondary" onClick={() => setAdding(true)}>Add someone you look after</Button>}
          />
        </Card>
      ) : (
        <div className="flex flex-col gap-6">
          {dashboard.data.people.map((p) => <PersonCard key={p.patient_id} person={p} compact />)}
        </div>
      )}
      {adding && <AddDependantDialog onClose={() => setAdding(false)} />}
    </>
  );
}

function InvitationCard({ invitation }: { invitation: CareLink }) {
  const respond = useRespondToInvitation();
  const toast = useToast();
  const act = async (decision: "accept" | "decline") => {
    try {
      await respond.mutateAsync({ relationship_id: invitation.relationship_id, decision });
      toast.success(decision === "accept" ? "Invitation accepted" : "Invitation declined");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };
  return (
    <Alert tone="info" title={`${invitation.patient_name ?? "Someone"} invited you to be their caregiver`}>
      <p>You would be able to:</p>
      <ul className="mt-1 list-inside list-disc">
        {invitation.scopes.map((s) => <li key={s}>{scopeLabel(s)}</li>)}
      </ul>
      {invitation.expires_at && <p className="mt-1">Until {formatDate(invitation.expires_at.slice(0, 10))}.</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => void act("accept")} loading={respond.isPending}>Accept</Button>
        <Button size="sm" variant="secondary" onClick={() => void act("decline")}>Decline</Button>
      </div>
    </Alert>
  );
}

function NotShared() {
  return (
    <p className="flex items-center gap-2 text-sm text-muted">
      <Lock className="size-4" aria-hidden /> Not shared with you
    </p>
  );
}

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">{icon}{title}</h3>
      {children}
    </section>
  );
}

/** One person's summary. Every section distinguishes "not shared" from "nothing to show". */
export function PersonCard({ person, compact = false }: { person: PersonSummary; compact?: boolean }) {
  const can = (p: string) => person.permissions.includes(p);
  const open = (person.today_doses ?? []).filter((d) => TODO_DOSE.includes(d.status));
  const done = (person.today_doses ?? []).length - open.length;
  return (
    <Card
      title={
        <span className="flex flex-wrap items-center gap-2">
          {person.name}
          <span className="text-sm font-normal text-muted">you are their {humanize(person.relationship_type).toLowerCase()}</span>
          {person.is_guardian && <Badge tone="info">Guardian</Badge>}
        </span>
      }
      action={compact ? <Link to={`/care/${person.patient_id}`} className="text-sm font-semibold text-accent underline">Open record</Link> : undefined}
    >
      <div className="grid gap-6 md:grid-cols-2">
        <Section title="Today's medicines" icon={<Pill className="size-4" aria-hidden />}>
          {person.today_doses === null ? (
            <NotShared />
          ) : person.today_doses.length === 0 ? (
            <p className="text-sm text-muted">Nothing scheduled today.</p>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-muted">{done} of {person.today_doses.length} done</p>
              {open.slice(0, compact ? 3 : undefined).map((d) => (
                <DoseCard key={d.id} dose={d} patientId={person.patient_id} canAct={can("log_doses")} />
              ))}
              {compact && open.length > 3 && <p className="text-sm text-muted">and {open.length - 3} more</p>}
            </div>
          )}
        </Section>

        <Section title="Missed in the last 7 days" icon={<AlertTriangle className="size-4" aria-hidden />}>
          {person.missed_doses === null ? (
            <NotShared />
          ) : person.missed_doses.length === 0 ? (
            <p className="text-sm text-muted">No missed doses.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm">
              {person.missed_doses.slice(0, compact ? 5 : undefined).map((d) => (
                <li key={d.id} className="flex justify-between gap-3">
                  <span className="font-medium">{d.medication_name}</span>
                  <span className="text-muted">{formatDate(d.scheduled_at?.slice(0, 10))} {formatTime(d.scheduled_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Appointments and follow-ups" icon={<CalendarDays className="size-4" aria-hidden />}>
          {person.appointments === null ? (
            <NotShared />
          ) : person.appointments.length === 0 && (person.follow_ups ?? []).length === 0 ? (
            <p className="text-sm text-muted">Nothing upcoming.</p>
          ) : (
            <ul className="flex flex-col gap-2 text-sm">
              {person.appointments.slice(0, 3).map((a) => (
                <li key={a.id}>
                  <p className="font-medium">{formatDateTime(a.starts_at)} <StatusBadge status={a.status} /></p>
                  <p className="text-muted">{a.doctor_name ?? "Doctor"} · {humanize(a.mode)}</p>
                </li>
              ))}
              {(person.follow_ups ?? []).slice(0, 3).map((f) => (
                <li key={f.id}>
                  <p className="font-medium">Follow-up by {formatDate(f.due_date)}</p>
                  <p className="text-muted">{f.doctor_name ?? "Doctor"}{f.reason ? ` · ${f.reason}` : ""}</p>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Recent reports" icon={<FileText className="size-4" aria-hidden />}>
          {person.recent_reports === null ? (
            <NotShared />
          ) : person.recent_reports.length === 0 ? (
            <p className="text-sm text-muted">No reports yet.</p>
          ) : (
            <ul className="flex flex-col gap-1 text-sm">
              {person.recent_reports.map((r) => (
                <li key={r.id} className="flex justify-between gap-3">
                  <span className="font-medium">{r.lab_name ?? "Report"}</span>
                  <span className="text-muted">{formatDate((r.collected_at ?? r.created_at).slice(0, 10))}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>
    </Card>
  );
}

// --- Add a dependant -----------------------------------------------------------------------------

function ageFromIso(dob: string): number | null {
  if (!dob) return null;
  const born = new Date(`${dob}T00:00:00`);
  const now = new Date();
  let age = now.getFullYear() - born.getFullYear();
  if (now.getMonth() < born.getMonth() || (now.getMonth() === born.getMonth() && now.getDate() < born.getDate())) age -= 1;
  return age;
}

export function AddDependantDialog({ onClose }: { onClose: () => void }) {
  const create = useCreateDependant();
  const toast = useToast();
  const navigate = useNavigate();
  const [given, setGiven] = useState("");
  const [family, setFamily] = useState("");
  const [dob, setDob] = useState("");
  const [sex, setSex] = useState<Schemas["SexAtBirth"]>("unknown");
  const [relationship, setRelationship] = useState<Schemas["CaregiverRelationshipType"]>("parent");
  const [basis, setBasis] = useState<Schemas["DependantBasis"] | "">("");
  const [declared, setDeclared] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const age = ageFromIso(dob);
  const minor = age !== null && age < 18;
  const bases = age === null ? [] : DEPENDANT_BASES.filter((b) => b.minor === minor);

  const save = async () => {
    setError(null);
    if (!given.trim()) return setError("Enter their first name.");
    if (!dob || age === null || age < 0) return setError("Enter their date of birth.");
    if (!basis || !bases.some((b) => b.value === basis)) return setError("Choose how you are authorised to manage their record.");
    if (!declared) return setError("Confirm the declaration to continue.");
    try {
      const link = await create.mutateAsync({
        given_name: given.trim(),
        family_name: family.trim() || null,
        date_of_birth: dob,
        sex_at_birth: sex,
        relationship_type: relationship,
        basis,
        declaration_accepted: true,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata",
      });
      toast.success(`${given.trim()} added`);
      onClose();
      void navigate(`/care/${link.patient_id}`);
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Add someone you look after"
      description="For a child, or an adult who cannot manage their own health record. If they can use a phone, it is better that they create their own account and invite you."
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => void save()} loading={create.isPending}>Add</Button></>}
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="First name" required>{(p) => <Input {...p} value={given} onChange={(e) => setGiven(e.target.value)} />}</Field>
          <Field label="Last name">{(p) => <Input {...p} value={family} onChange={(e) => setFamily(e.target.value)} />}</Field>
        </div>
        <Field label="Date of birth" required>
          {(p) => <Input {...p} type="date" max={todayIso()} value={dob} onChange={(e) => { setDob(e.target.value); setBasis(""); }} />}
        </Field>
        <Field label="Sex at birth">
          {(p) => (
            <Select {...p} value={sex} onChange={(e) => setSex(e.target.value as Schemas["SexAtBirth"])}>
              <option value="female">Female</option>
              <option value="male">Male</option>
              <option value="intersex">Intersex</option>
              <option value="unknown">Prefer not to say</option>
            </Select>
          )}
        </Field>
        <Field label="You are their">
          {(p) => (
            <Select {...p} value={relationship} onChange={(e) => setRelationship(e.target.value as Schemas["CaregiverRelationshipType"])}>
              <option value="parent">Parent</option>
              <option value="child">Son or daughter</option>
              <option value="spouse_partner">Spouse or partner</option>
              <option value="sibling">Brother or sister</option>
              <option value="other_relative">Other relative</option>
              <option value="legal_guardian">Legal guardian</option>
              <option value="professional_carer">Professional carer</option>
              <option value="other">Other</option>
            </Select>
          )}
        </Field>
        {age !== null && age >= 0 && (
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">
              {minor ? `They are ${age} (under 18). How are you responsible for them?` : "They are an adult. How are you authorised to manage their record?"}
            </legend>
            {bases.map((b) => (
              <label key={b.value} className="flex min-h-11 items-center gap-3 rounded-lg border border-line px-3 text-sm">
                <input type="radio" name="basis" value={b.value} checked={basis === b.value} onChange={() => setBasis(b.value)} className="size-4 accent-[var(--hio-accent)]" />
                {b.label}
              </label>
            ))}
          </fieldset>
        )}
        <Checkbox
          label="I confirm this is true and that I am allowed to manage their health information"
          description="This declaration is recorded. You will be their guardian on Health Io; you cannot change anything a doctor records."
          checked={declared}
          onChange={(e) => setDeclared(e.target.checked)}
        />
      </div>
    </Dialog>
  );
}

// --- One person ---------------------------------------------------------------------------------

export function CarePersonPage() {
  const { patientId } = useActivePatient();
  const dashboard = useCareDashboard();
  const links = useCaregiving();
  const person = dashboard.data?.people.find((p) => p.patient_id === patientId);
  const link = links.data?.find((l) => l.patient_id === patientId && l.status === "active");
  const [leaving, setLeaving] = useState(false);

  return (
    <>
      <PageHeader
        title={person?.name ?? "Overview"}
        description={link?.is_guardian ? `You are their guardian (${basisLabel(link.guardian_basis) ?? "declared"}).` : "You help as a caregiver with the permissions they gave you."}
        actions={link && <Button variant="ghost" onClick={() => setLeaving(true)}>Stop being their caregiver</Button>}
      />
      {dashboard.isPending ? (
        <SkeletonList rows={3} />
      ) : dashboard.isError ? (
        <ErrorState message={errorMessage(dashboard.error)} onRetry={() => void dashboard.refetch()} />
      ) : person ? (
        <PersonCard person={person} />
      ) : (
        <Card><EmptyState title="No longer available" description="You no longer have access to this person's record." /></Card>
      )}
      {leaving && link && <LeaveDialog link={link} onClose={() => setLeaving(false)} />}
    </>
  );
}

function LeaveDialog({ link, onClose }: { link: CareLink; onClose: () => void }) {
  const leave = useLeave();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    try {
      await leave.mutateAsync(link.relationship_id);
      onClose();
      void navigate("/care");
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Stop caring for ${link.patient_name ?? "this person"}?`}
      description="Your access ends immediately. They (or their guardian) can invite you again later."
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button variant="danger" onClick={() => void go()} loading={leave.isPending}>Stop</Button></>}
    >
      {error ? <Alert tone="danger">{error}</Alert> : <p className="text-sm text-muted">This is recorded in their activity log.</p>}
    </Dialog>
  );
}

export function CareRemindersPage() {
  const { patientId } = useActivePatient();
  return (
    <>
      <PageHeader title="Reminder settings" description="How and when medicine reminders are sent for this person." />
      <div className="flex max-w-2xl flex-col gap-6">
        <ReminderCard patientId={patientId} />
        <DevicePushCard />
      </div>
    </>
  );
}
