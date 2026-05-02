import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Codelists",
  description:
    "Authenticated workspace for reviewing and approving clinical codelists. Sign-in required.",
  robots: { index: false, follow: false },
  alternates: { canonical: null },
};

export default function CodelistsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
