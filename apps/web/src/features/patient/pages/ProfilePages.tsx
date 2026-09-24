/** Things the patient owns and manages: profile, emergency info, caregivers, settings. */
import { Lock, Phone, Plus, ShieldAlert, Trash2, UserPlus, Users } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { PageHeader } from "@/components/layout/PortalShell";
import { Alert, Badge, Button, Card, Checkbox, Dialog, EmptyState, Field, Input, Select, Stat, Textarea, useToast } from "@/components/ui";
import { useSession, useMe } from "@/features/auth/session";
import { useAdherence, useMedicalHistory, useMedications } from "@/features/chart/api";
import { DefinitionList, NotShared, QueryState } from "@/features/chart/shared";
import { errorMessage, type Schemas } from "@/lib/api";
import { formatDate, formatDateTime, humanize, todayIso } from "@/lib/format";

import {
  useAddEmergencyContact,
  useCaregiverActivity,
  useCaregivers,
  useChangePassword,
  useEmergencyContacts,
  useEmergencyProfile,
  useInviteCaregiver,
  useProfile,
  useReminderPreferences,
  useRemoveEmergencyContact,
  useRevokeCaregiver,
  useRevokeSession,
  useSaveEmergencyProfile,
  useSaveReminderPreferences,
  useSessions,
  useSetCaregiverScopes,
  useUpdateAccount,
  useUpdateProfile,
  type CaregiverLink,
  type ReminderPreferences,
} from "../api";
import { useActivePatient, usePatientId } from "../context";
import { useAiConsent, useSetAiConsent } from "@/features/scans/api";

import { setLargeText, useLargeText } from "../largeText";
import { DEPENDANT_BASES, SCOPES, type Scope } from "../scopes";

const BLOOD_GROUPS: Schemas["BloodGroup"][] = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-", "unknown"];

// --- My health ---------------------------------------------------------------------------------------

export function MyHealthPage() {
  const { patientId: pid, base, mode, can } = useActivePatient();
  const profile = useProfile(pid, can("view_profile"));
  const history = useMedicalHistory(pid, can("view_medical_history"));
  const meds = useMedications(pid, can("view_medications"));
  const adherence = useAdherence(pid, can("view_adherence"));
  const self = mode === "self";
  const [editing, setEditing] = useState(false);

  return (
    <>
      <PageHeader title={self ? "My health" : "Health summary"} description="A summary of the health record." />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card
          title={self ? "About me" : "About them"}
          action={can("edit_profile") && <Button size="sm" variant="secondary" onClick={() => setEditing(true)}>Edit</Button>}
        >
          {!can("view_profile") ? (
            <NotShared what="Basic details" />
          ) : (
          <QueryState query={profile} what="Profile">
            {(p) => (
              <DefinitionList
                items={[
                  ["Name", p.display_name],
                  ["Date of birth", p.date_of_birth ? `${formatDate(p.date_of_birth)}${p.age_years != null ? ` (${p.age_years} years)` : ""}` : "Not set"],
                  ["Sex at birth", humanize(p.sex_at_birth)],
                  ["Blood group", p.blood_group === "unknown" ? "Not known" : p.blood_group],
                  ["Language", p.preferred_language === "hi" ? "हिन्दी (Hindi)" : "English"],
                ]}
              />
            )}
          </QueryState>
          )}
        </Card>
        <Card title="At a glance">
          <div className="grid grid-cols-2 gap-4">
            <Stat label="Current medicines" value={meds.data ? meds.data.filter((m) => m.status === "active").length : "—"} />
            <Stat label="Allergies" value={history.data ? history.data.allergies.length : "—"} />
            <Stat label="Conditions" value={history.data ? history.data.conditions.length : "—"} />
            <Stat label="Doses taken (30 days)" value={adherencePercent(adherence.data)} />
          </div>
          <div className="mt-4 flex flex-wrap gap-3 text-sm">
            <Link className="font-medium text-accent underline-offset-4 hover:underline" to={`${base}/history`}>Medical history</Link>
            <Link className="font-medium text-accent underline-offset-4 hover:underline" to={`${base}/medications`}>Medicines</Link>
            <Link className="font-medium text-accent underline-offset-4 hover:underline" to={`${base}/emergency`}>Emergency profile</Link>
          </div>
        </Card>
      </div>
      {editing && profile.data && <ProfileDialog patientId={pid} profile={profile.data} onClose={() => setEditing(false)} />}
    </>
  );
}

function adherencePercent(a: Schemas["AdherenceOut"] | undefined): string {
  if (!a || a.total_recorded === 0) return "—";
  const taken = a.lines.reduce((n, l) => n + l.taken, 0);
  return `${Math.round((taken / a.total_recorded) * 100)}%`;
}

function ProfileDialog({ patientId, profile, onClose }: { patientId: string; profile: Schemas["ProfileOut"]; onClose: () => void }) {
  const update = useUpdateProfile(patientId);
  const toast = useToast();
  const [given, setGiven] = useState(profile.given_name);
  const [family, setFamily] = useState(profile.family_name ?? "");
  const [dob, setDob] = useState(profile.date_of_birth ?? "");
  const [sex, setSex] = useState(profile.sex_at_birth as Schemas["SexAtBirth"]);
  const [blood, setBlood] = useState(profile.blood_group as Schemas["BloodGroup"]);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    if (!given.trim()) return setError("Enter your first name.");
    try {
      await update.mutateAsync({
        given_name: given.trim(),
        family_name: family.trim() || null,
        date_of_birth: dob || null,
        sex_at_birth: sex,
        blood_group: blood,
      });
      toast.success("Profile saved");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Edit my details"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => void save()} loading={update.isPending}>Save</Button></>}
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="First name" required>{(p) => <Input {...p} autoComplete="given-name" value={given} onChange={(e) => setGiven(e.target.value)} />}</Field>
          <Field label="Last name">{(p) => <Input {...p} autoComplete="family-name" value={family} onChange={(e) => setFamily(e.target.value)} />}</Field>
        </div>
        <Field label="Date of birth">{(p) => <Input {...p} type="date" max={todayIso()} value={dob} onChange={(e) => setDob(e.target.value)} />}</Field>
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
        <Field label="Blood group" hint="Only if you know it from a test.">
          {(p) => (
            <Select {...p} value={blood} onChange={(e) => setBlood(e.target.value as Schemas["BloodGroup"])}>
              {BLOOD_GROUPS.map((b) => <option key={b} value={b}>{b === "unknown" ? "I don't know" : b}</option>)}
            </Select>
          )}
        </Field>
      </div>
    </Dialog>
  );
}

// --- Emergency profile -----------------------------------------------------------------------------

export function EmergencyPage() {
  const pid = usePatientId();
  const profile = useEmergencyProfile(pid);
  const contacts = useEmergencyContacts(pid);
  const save = useSaveEmergencyProfile(pid);
  const remove = useRemoveEmergencyContact(pid);
  const toast = useToast();
  // Unsaved edits layered over the saved profile (no effect needed to seed a form).
  const [edits, setEdits] = useState<Partial<Schemas["EmergencyProfileModel"]>>({});
  const [adding, setAdding] = useState(false);
  const form: Schemas["EmergencyProfileModel"] | null = profile.data
    ? {
        show_allergies: profile.data.show_allergies,
        show_blood_group: profile.data.show_blood_group,
        show_conditions: profile.data.show_conditions,
        show_medications: profile.data.show_medications,
        critical_information: profile.data.critical_information ?? null,
        organ_donor: profile.data.organ_donor ?? null,
        advance_directive: profile.data.advance_directive ?? null,
        ...edits,
      }
    : null;

  const submit = async () => {
    if (!form) return;
    try {
      await save.mutateAsync(form);
      toast.success("Emergency profile saved");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  const set = <K extends keyof Schemas["EmergencyProfileModel"]>(k: K, v: Schemas["EmergencyProfileModel"][K]) =>
    setEdits((e) => ({ ...e, [k]: v }));

  return (
    <>
      <PageHeader
        title="Emergency profile"
        description="Information that could help responders in an emergency. You decide what is included."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="What to show in an emergency">
          <QueryState query={profile} what="Emergency profile">
            {() =>
              form && (
                <form
                  className="flex flex-col gap-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void submit();
                  }}
                >
                  <fieldset className="flex flex-col gap-3">
                    <legend className="mb-2 text-sm font-medium">Include from my record</legend>
                    <Checkbox label="Blood group" checked={form.show_blood_group} onChange={(e) => set("show_blood_group", e.target.checked)} />
                    <Checkbox label="Allergies" checked={form.show_allergies} onChange={(e) => set("show_allergies", e.target.checked)} />
                    <Checkbox label="Conditions" checked={form.show_conditions} onChange={(e) => set("show_conditions", e.target.checked)} />
                    <Checkbox label="Current medicines" checked={form.show_medications} onChange={(e) => set("show_medications", e.target.checked)} />
                  </fieldset>
                  <Field label="Important information for responders" hint="For example: “I have a pacemaker” or “I use an inhaler”.">
                    {(p) => <Textarea {...p} rows={3} maxLength={1000} value={form.critical_information ?? ""} onChange={(e) => set("critical_information", e.target.value || null)} />}
                  </Field>
                  <Field label="Organ donor">
                    {(p) => (
                      <Select {...p} value={form.organ_donor ?? ""} onChange={(e) => set("organ_donor", (e.target.value || null) as Schemas["OrganDonorStatus"] | null)}>
                        <option value="">Not stated</option>
                        <option value="yes">Yes</option>
                        <option value="no">No</option>
                        <option value="undecided">Undecided</option>
                      </Select>
                    )}
                  </Field>
                  <Field label="Advance directive" hint="Where to find it, or who holds it.">
                    {(p) => <Textarea {...p} rows={2} maxLength={1000} value={form.advance_directive ?? ""} onChange={(e) => set("advance_directive", e.target.value || null)} />}
                  </Field>
                  {profile.data?.last_reviewed_at && <p className="text-xs text-muted">Last reviewed {formatDateTime(profile.data.last_reviewed_at)}</p>}
                  <div><Button type="submit" loading={save.isPending}>Save</Button></div>
                </form>
              )
            }
          </QueryState>
        </Card>
        <Card
          title="Emergency contacts"
          action={<Button size="sm" variant="secondary" icon={<Plus className="size-4" />} onClick={() => setAdding(true)}>Add</Button>}
        >
          <QueryState
            query={contacts}
            what="Contacts"
            isEmpty={(c) => c.length === 0}
            empty={<EmptyState icon={<Phone className="size-5" />} title="No emergency contacts" description="Add someone who should be called in an emergency." />}
          >
            {(list) => (
              <ul className="divide-y divide-line">
                {list.map((c) => (
                  <li key={c.id} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                    <div>
                      <p className="font-medium">{c.name}{c.relationship_label ? <span className="font-normal text-muted"> · {c.relationship_label}</span> : null}</p>
                      <a className="text-sm text-accent" href={`tel:${c.phone}`}>{c.phone}</a>
                      {c.notify_on_sos && <p className="text-xs text-muted">Alerted when you send an SOS</p>}
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove ${c.name}`}
                      onClick={() => remove.mutate(c.id, { onError: (err) => toast.error(errorMessage(err)) })}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </QueryState>
        </Card>
      </div>
      {adding && <ContactDialog patientId={pid} onClose={() => setAdding(false)} />}
    </>
  );
}

function ContactDialog({ patientId, onClose }: { patientId: string; onClose: () => void }) {
  const add = useAddEmergencyContact(patientId);
  const toast = useToast();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [label, setLabel] = useState("");
  const [notify, setNotify] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!name.trim() || !phone.trim()) return setError("Enter a name and phone number.");
    try {
      await add.mutateAsync({ name: name.trim(), phone: phone.trim(), relationship_label: label.trim() || null, notify_on_sos: notify });
      toast.success("Contact added");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title="Add an emergency contact"
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => void save()} loading={add.isPending}>Add</Button></>}
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <Field label="Name" required>{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Phone number" required hint="Include the country code, e.g. +91 98765 43210">
          {(p) => <Input {...p} type="tel" autoComplete="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />}
        </Field>
        <Field label="Relationship">{(p) => <Input {...p} placeholder="e.g. Daughter" value={label} onChange={(e) => setLabel(e.target.value)} />}</Field>
        <Checkbox label="Alert this person when I send an SOS" checked={notify} onChange={(e) => setNotify(e.target.checked)} />
      </div>
    </Dialog>
  );
}

// --- Caregivers ------------------------------------------------------------------------------------

const RELATIONSHIPS: Schemas["CaregiverRelationshipType"][] = [
  "parent", "child", "spouse_partner", "sibling", "other_relative", "friend", "professional_carer", "legal_guardian", "other",
];

const ACTIVITY_LABELS: Record<string, string> = {
  "caregiver.invited": "Was invited",
  "caregiver.accepted": "Accepted the invitation",
  "caregiver.declined": "Declined the invitation",
  "caregiver.left": "Stopped being a caregiver",
  "caregiver.dashboard_view": "Opened their caregiver dashboard",
  "access.denied": "Tried something they are not allowed to do",
};

function activityLabel(action: string): string {
  if (ACTIVITY_LABELS[action]) return ACTIVITY_LABELS[action];
  const [thing = "", verb = ""] = action.split(".");
  const what = humanize(thing).toLowerCase();
  if (verb === "list" || verb === "read" || verb === "view") return `Viewed ${what}`;
  return `${humanize(verb)} · ${what}`;
}

export function CaregiversPage() {
  const { patientId: pid, mode } = useActivePatient();
  const self = mode === "self";
  const caregivers = useCaregivers(pid);
  const revoke = useRevokeCaregiver(pid);
  const toast = useToast();
  const [inviting, setInviting] = useState(false);
  const [editing, setEditing] = useState<CaregiverLink | null>(null);
  const [revoking, setRevoking] = useState<CaregiverLink | null>(null);

  const doRevoke = async () => {
    if (!revoking) return;
    try {
      await revoke.mutateAsync(revoking.relationship_id);
      toast.success("Access removed");
      setRevoking(null);
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };

  return (
    <>
      <PageHeader
        title="Caregivers"
        description={
          self
            ? "Family or carers who help you. You choose exactly what each person can see and do, and you can change it at any time."
            : "Everyone who helps look after this person. As a guardian you choose what each of them can see and do."
        }
        actions={<Button icon={<UserPlus className="size-4" />} onClick={() => setInviting(true)}>Invite a caregiver</Button>}
      />
      <div className="mb-6">
        <Alert tone="info">
          <span className="inline-flex items-start gap-2">
            <Lock className="mt-0.5 size-4 shrink-0" aria-hidden />
            Caregivers can never change a doctor’s records or prescriptions, or delete medical records. Everything they open or do is recorded.
          </span>
        </Alert>
      </div>
      <QueryState
        query={caregivers}
        what="Caregivers"
        isEmpty={(c) => c.filter((l) => l.status === "active" || l.status === "invited").length === 0}
        empty={<Card><EmptyState icon={<Users className="size-5" />} title="No caregivers" description="Invite someone you trust to help with reminders and appointments." /></Card>}
      >
        {(list) => (
          <div className="grid gap-4 md:grid-cols-2">
            {list.filter((l) => l.status === "active" || l.status === "invited").map((l) => (
              <Card key={l.relationship_id}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold">{l.caregiver_name ?? "Caregiver"}</p>
                    <p className="text-sm text-muted">
                      {humanize(l.relationship_type)}
                      {l.is_guardian && " · guardian"}
                      {l.expires_at && ` · until ${formatDate(l.expires_at.slice(0, 10))}`}
                    </p>
                  </div>
                  <Badge tone={l.status === "active" ? "success" : "warning"}>{l.status === "active" ? "Active" : "Invitation sent"}</Badge>
                </div>
                <p className="mt-3 text-sm font-medium">Can:</p>
                <ul className="mt-1 list-inside list-disc text-sm text-muted">
                  {SCOPES.filter((s) => l.scopes.includes(s.value)).map((s) => <li key={s.value}>{s.label}</li>)}
                </ul>
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button size="sm" variant="secondary" onClick={() => setEditing(l)}>Change access</Button>
                  <Button size="sm" variant="ghost" onClick={() => setRevoking(l)}>Remove</Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </QueryState>
      <CaregiverActivity patientId={pid} />
      {inviting && <InviteDialog patientId={pid} allowGuardian={!self} onClose={() => setInviting(false)} />}
      {editing && <ScopesDialog patientId={pid} link={editing} onClose={() => setEditing(null)} />}
      <Dialog
        open={revoking !== null}
        onClose={() => setRevoking(null)}
        title={`Remove ${revoking?.caregiver_name ?? "this caregiver"}?`}
        description="They will immediately lose all access."
        footer={<><Button variant="secondary" onClick={() => setRevoking(null)}>Cancel</Button><Button variant="danger" onClick={() => void doRevoke()} loading={revoke.isPending}>Remove access</Button></>}
      >
        <p className="text-sm text-muted">You can invite them again later.</p>
      </Dialog>
    </>
  );
}

function CaregiverActivity({ patientId }: { patientId: string }) {
  const activity = useCaregiverActivity(patientId);
  return (
    <Card title="What caregivers did recently" className="mt-6" bodyClassName="p-0">
      <QueryState
        query={activity}
        what="Caregiver activity"
        isEmpty={(a) => a.length === 0}
        empty={<p className="p-4 text-sm text-muted">No caregiver activity yet.</p>}
      >
        {(rows) => (
          <ul className="divide-y divide-line">
            {rows.slice(0, 30).map((r, i) => (
              <li key={`${r.occurred_at}-${i}`} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 text-sm">
                <span>
                  <span className="font-medium">{r.caregiver_name ?? "A caregiver"}</span> · {activityLabel(r.action)}
                  {r.outcome === "denied" && <Badge tone="warning">Blocked</Badge>}
                </span>
                <span className="text-muted">{formatDateTime(r.occurred_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </QueryState>
    </Card>
  );
}

function ScopeChecklist({ value, onChange, guardian }: { value: Scope[]; onChange: (v: Scope[]) => void; guardian: boolean }) {
  const groups = ["See", "Do", "Manage"] as const;
  return (
    <div className="flex flex-col gap-4">
      {groups.map((g) => {
        const items = SCOPES.filter((s) => s.group === g && (g !== "Manage" || guardian));
        if (items.length === 0) return null;
        return (
          <fieldset key={g} className="flex flex-col gap-3">
            <legend className="mb-2 text-sm font-medium">{g === "See" ? "What can they see?" : g === "Do" ? "What can they do?" : "Guardian permissions"}</legend>
            {items.map((s) => (
              <Checkbox
                key={s.value}
                label={s.label}
                checked={value.includes(s.value)}
                onChange={(e) => onChange(e.target.checked ? [...value, s.value] : value.filter((v) => v !== s.value))}
              />
            ))}
          </fieldset>
        );
      })}
    </div>
  );
}

function InviteDialog({ patientId, allowGuardian, onClose }: { patientId: string; allowGuardian: boolean; onClose: () => void }) {
  const invite = useInviteCaregiver(patientId);
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [relationship, setRelationship] = useState<Schemas["CaregiverRelationshipType"]>("child");
  const [scopes, setScopes] = useState<Scope[]>(["view_profile", "view_medications", "receive_alerts"]);
  const [guardian, setGuardian] = useState(false);
  const [basis, setBasis] = useState<string>(DEPENDANT_BASES[0]!.value);
  const [expires, setExpires] = useState("");
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (!email.trim()) return setError("Enter their email address.");
    if (scopes.length === 0) return setError("Choose at least one thing they can do.");
    try {
      await invite.mutateAsync({
        caregiver_email: email.trim(),
        relationship_type: relationship,
        scopes: guardian ? scopes : scopes.filter((s) => s !== "manage_caregivers"),
        is_guardian: guardian,
        guardian_basis: guardian ? basis : null,
        expires_at: expires ? new Date(`${expires}T23:59:59`).toISOString() : null,
      });
      toast.success("Invitation sent");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title="Invite a caregiver"
      description="They need a Health Io account. They get access only after they accept, and only to what you tick below."
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => void save()} loading={invite.isPending}>Send invitation</Button></>}
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <Field label="Their email" required>{(p) => <Input {...p} type="email" autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} />}</Field>
        <Field label={allowGuardian ? "They are this person's" : "They are my"}>
          {(p) => (
            <Select {...p} value={relationship} onChange={(e) => setRelationship(e.target.value as Schemas["CaregiverRelationshipType"])}>
              {RELATIONSHIPS.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}
            </Select>
          )}
        </Field>
        {allowGuardian && (
          <>
            <Checkbox
              label="They are also a guardian"
              description="For example the other parent. Guardians can manage caregivers too."
              checked={guardian}
              onChange={(e) => setGuardian(e.target.checked)}
            />
            {guardian && (
              <Field label="On what basis?">
                {(p) => (
                  <Select {...p} value={basis} onChange={(e) => setBasis(e.target.value)}>
                    {DEPENDANT_BASES.map((b) => <option key={b.value} value={b.value}>{b.other}</option>)}
                  </Select>
                )}
              </Field>
            )}
          </>
        )}
        <ScopeChecklist value={scopes} onChange={setScopes} guardian={guardian} />
        <Field label="Access ends on (optional)">{(p) => <Input {...p} type="date" min={todayIso(1)} value={expires} onChange={(e) => setExpires(e.target.value)} />}</Field>
      </div>
    </Dialog>
  );
}

function ScopesDialog({ patientId, link, onClose }: { patientId: string; link: CaregiverLink; onClose: () => void }) {
  const setScopesMutation = useSetCaregiverScopes(patientId);
  const toast = useToast();
  const [scopes, setScopes] = useState<Scope[]>(link.scopes);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    if (scopes.length === 0) return setError("Choose at least one, or remove the caregiver instead.");
    try {
      await setScopesMutation.mutateAsync({ relationship_id: link.relationship_id, scopes });
      toast.success("Access updated");
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title={`What ${link.caregiver_name ?? "this caregiver"} can do`}
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={() => void save()} loading={setScopesMutation.isPending}>Save</Button></>}
    >
      <div className="flex flex-col gap-4">
        {error && <Alert tone="danger">{error}</Alert>}
        <ScopeChecklist value={scopes} onChange={setScopes} guardian={link.is_guardian} />
      </div>
    </Dialog>
  );
}

// --- Settings --------------------------------------------------------------------------------------

const TIMEZONES = ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "America/New_York", "UTC"];

export function SettingsPage() {
  const pid = usePatientId();
  return (
    <>
      <PageHeader title="Settings" />
      <div className="grid gap-6 lg:grid-cols-2">
        <AccountCard />
        <ReminderCard patientId={pid} />
        <DisplayCard />
        <AiReadingCard patientId={pid} />
        <PasswordCard />
        <SessionsCard />
      </div>
    </>
  );
}

function AccountCard() {
  const me = useMe();
  const update = useUpdateAccount();
  const toast = useToast();
  const [name, setName] = useState(me.display_name);
  const [tz, setTz] = useState(me.timezone);
  const [lang, setLang] = useState(me.preferred_language as "en" | "hi");
  const zones = TIMEZONES.includes(me.timezone) ? TIMEZONES : [me.timezone, ...TIMEZONES];
  const save = async () => {
    try {
      await update.mutateAsync({ display_name: name.trim() || null, timezone: tz, preferred_language: lang });
      toast.success("Account saved");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };
  return (
    <Card title="Account">
      <div className="flex flex-col gap-4">
        <Field label="Name shown in the app">{(p) => <Input {...p} value={name} onChange={(e) => setName(e.target.value)} />}</Field>
        <Field label="Email">{(p) => <Input {...p} value={me.email ?? ""} disabled />}</Field>
        <Field label="Time zone" hint="Reminders follow this time zone.">
          {(p) => <Select {...p} value={tz} onChange={(e) => setTz(e.target.value)}>{zones.map((z) => <option key={z} value={z}>{z}</option>)}</Select>}
        </Field>
        <Field label="Language">
          {(p) => (
            <Select {...p} value={lang} onChange={(e) => setLang(e.target.value as "en" | "hi")}>
              <option value="en">English</option>
              <option value="hi">हिन्दी (Hindi)</option>
            </Select>
          )}
        </Field>
        <div><Button onClick={() => void save()} loading={update.isPending}>Save</Button></div>
      </div>
    </Card>
  );
}

export function ReminderCard({ patientId }: { patientId: string }) {
  const prefs = useReminderPreferences(patientId);
  const save = useSaveReminderPreferences(patientId);
  const toast = useToast();
  const [edits, setEdits] = useState<Partial<ReminderPreferences>>({});
  const form: ReminderPreferences | null = prefs.data ? { ...prefs.data, ...edits } : null;
  const set = <K extends keyof ReminderPreferences>(k: K, v: ReminderPreferences[K]) => setEdits((e) => ({ ...e, [k]: v }));
  const submit = async () => {
    if (!form) return;
    try {
      await save.mutateAsync({ ...form, quiet_hours_start: form.quiet_hours_start || null, quiet_hours_end: form.quiet_hours_end || null });
      toast.success("Reminder settings saved");
    } catch (err) {
      toast.error(errorMessage(err));
    }
  };
  return (
    <Card title="Medicine reminders">
      <QueryState query={prefs} what="Reminder settings">
        {() =>
          form && (
            <div className="flex flex-col gap-4">
              <Checkbox label="Send me medicine reminders" checked={form.reminders_enabled} onChange={(e) => set("reminders_enabled", e.target.checked)} />
              <fieldset className="flex flex-col gap-3" disabled={!form.reminders_enabled}>
                <legend className="mb-2 text-sm font-medium">How to remind me</legend>
                <Checkbox label="App notification" checked={form.channel_push} onChange={(e) => set("channel_push", e.target.checked)} />
                <Checkbox label="Text message (SMS)" checked={form.channel_sms} onChange={(e) => set("channel_sms", e.target.checked)} />
                <Checkbox label="Email" checked={form.channel_email} onChange={(e) => set("channel_email", e.target.checked)} />
              </fieldset>
              <Checkbox
                label="Show medicine names in reminders"
                description="Off by default, so others who see your phone screen don’t learn what you take."
                checked={form.show_medicine_names}
                onChange={(e) => set("show_medicine_names", e.target.checked)}
              />
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Quiet hours from">{(p) => <Input {...p} type="time" value={form.quiet_hours_start?.slice(0, 5) ?? ""} onChange={(e) => set("quiet_hours_start", e.target.value || null)} />}</Field>
                <Field label="Quiet hours until">{(p) => <Input {...p} type="time" value={form.quiet_hours_end?.slice(0, 5) ?? ""} onChange={(e) => set("quiet_hours_end", e.target.value || null)} />}</Field>
              </div>
              <Field label="Snooze for">
                {(p) => (
                  <Select {...p} value={form.default_snooze_minutes} onChange={(e) => set("default_snooze_minutes", Number(e.target.value) as ReminderPreferences["default_snooze_minutes"])}>
                    {[5, 10, 15, 30, 60].map((m) => <option key={m} value={m}>{m} minutes</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Count a dose as missed after">
                {(p) => (
                  <Select {...p} value={form.missed_after_minutes} onChange={(e) => set("missed_after_minutes", Number(e.target.value))}>
                    {[30, 60, 120, 180, 240, 360, 720].map((m) => <option key={m} value={m}>{m < 60 ? `${m} minutes` : `${m / 60} hour${m > 60 ? "s" : ""}`}</option>)}
                  </Select>
                )}
              </Field>
              <Checkbox
                label="Tell my caregivers when I miss a dose"
                description="Only caregivers you allowed to receive alerts."
                checked={form.notify_caregivers_on_missed}
                onChange={(e) => set("notify_caregivers_on_missed", e.target.checked)}
              />
              <div><Button onClick={() => void submit()} loading={save.isPending}>Save</Button></div>
            </div>
          )
        }
      </QueryState>
    </Card>
  );
}

function AiReadingCard({ patientId }: { patientId: string }) {
  const consent = useAiConsent(patientId);
  const set = useSetAiConsent(patientId);
  const toast = useToast();
  if (consent.data && !consent.data.ai_available && !consent.data.granted) return null;
  return (
    <Card title="AI reading of prescriptions">
      <QueryState query={consent} what="AI setting">
        {(c) => (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-muted">{c.notice}</p>
            <Checkbox
              label="Allow AI to read prescription photos I upload"
              description={c.granted && c.granted_at ? `Allowed since ${formatDateTime(c.granted_at)}` : "Off: you can still type prescriptions in yourself."}
              checked={c.granted}
              disabled={set.isPending}
              onChange={(e) =>
                set.mutate(e.target.checked, {
                  onSuccess: (r) => toast.success(r.granted ? "AI reading allowed" : "AI reading turned off"),
                  onError: (err) => toast.error(errorMessage(err)),
                })
              }
            />
          </div>
        )}
      </QueryState>
    </Card>
  );
}

function DisplayCard() {
  const large = useLargeText();
  return (
    <Card title="Display">
      <Checkbox
        label="Larger text"
        description="Makes text and buttons bigger on this device."
        checked={large}
        onChange={(e) => setLargeText(e.target.checked)}
      />
    </Card>
  );
}

function PasswordCard() {
  const change = useChangePassword();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    setError(null);
    if (next.length < 12) return setError("Use at least 12 characters.");
    if (next !== confirm) return setError("The new passwords don’t match.");
    try {
      await change.mutateAsync({ current_password: current, new_password: next });
      toast.success("Password changed. Other devices were signed out.");
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  return (
    <Card title="Change password">
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        {error && <Alert tone="danger">{error}</Alert>}
        <Field label="Current password">{(p) => <Input {...p} type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />}</Field>
        <Field label="New password" hint="At least 12 characters.">{(p) => <Input {...p} type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />}</Field>
        <Field label="Confirm new password">{(p) => <Input {...p} type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />}</Field>
        <div><Button type="submit" loading={change.isPending}>Change password</Button></div>
      </form>
    </Card>
  );
}

function SessionsCard() {
  const sessions = useSessions();
  const revoke = useRevokeSession();
  const { signOut } = useSession();
  const toast = useToast();
  return (
    <Card title="Where you’re signed in">
      <QueryState query={sessions} what="Sessions">
        {(list) => (
          <ul className="divide-y divide-line">
            {list.map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                <div className="text-sm">
                  <p className="font-medium">{s.current ? "This device" : "Another device"}</p>
                  <p className="text-muted">Signed in {formatDateTime(s.created_at)} · last active {formatDateTime(s.last_seen_at)}</p>
                </div>
                {s.current ? (
                  <Button size="sm" variant="secondary" onClick={() => void signOut()}>Sign out</Button>
                ) : (
                  <Button size="sm" variant="ghost" onClick={() => revoke.mutate(s.id, { onError: (err) => toast.error(errorMessage(err)) })}>Sign out</Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </QueryState>
      <p className="mt-4 flex items-start gap-2 text-xs text-muted">
        <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden /> If you see a device you don’t recognise, sign it out and change your password.
      </p>
    </Card>
  );
}
