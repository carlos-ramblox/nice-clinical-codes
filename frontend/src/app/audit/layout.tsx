import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Audit log",
  description:
    "Authenticated audit log for approved codelists. Sign-in required.",
  robots: { index: false, follow: false },
  alternates: { canonical: null },
};

export default function AuditLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
