export type Portal = {
  href: string;
  title: string;
  description: string;
};

export const portals: Portal[] = [
  { href: "/patient", title: "Patient", description: "Medicines, reminders, records and reports." },
  { href: "/caregiver", title: "Caregiver", description: "Look after a family member's care." },
  { href: "/doctor", title: "Doctor", description: "Patients, prescriptions and follow-ups." },
  { href: "/admin", title: "Admin", description: "Verification, users and platform settings." },
];
