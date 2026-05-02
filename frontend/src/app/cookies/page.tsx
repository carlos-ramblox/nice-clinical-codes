import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Cookies",
  description:
    "Cookies statement for Clinical Code Discovery (clinicalcodes.uk). This page is under development.",
  alternates: { canonical: "/cookies" },
};

export default function Page() {
  return (
    <div className="max-w-3xl mx-auto px-6 py-16 text-center">
      <h1 className="text-2xl font-serif font-medium text-[#00436C] mb-3">Coming soon</h1>
      <p className="text-sm text-gray-600 mb-6">This page is under development.</p>
      <Link href="/" className="text-sm text-[#00436C] hover:underline">← Back to home</Link>
    </div>
  );
}
