import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in to clinicalcodes.uk to review and approve codelists.",
  robots: { index: false, follow: false },
  alternates: { canonical: null },
};

export default function LoginLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
