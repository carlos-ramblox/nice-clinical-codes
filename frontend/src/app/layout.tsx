import type { Metadata } from "next";
import { Inter, Lora } from "next/font/google";
import "./globals.css";
import { Nav } from "./Nav";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const lora = Lora({
  variable: "--font-lora",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

const SITE_URL = "https://clinicalcodes.uk";
const SITE_NAME = "Clinical Code Discovery";
const DESCRIPTION =
  "Open-source multi-source clinical codelist discovery with LLM-assisted scoring and clinician review. Cambridge PACE Data Science capstone.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: SITE_NAME,
    template: `%s | ${SITE_NAME}`,
  },
  description: DESCRIPTION,
  applicationName: SITE_NAME,
  keywords: [
    "clinical codelist",
    "codelist generation",
    "SNOMED CT",
    "ICD-10",
    "OPCS-4",
    "OMOP",
    "EHR phenotyping",
    "QOF",
    "OpenCodelists",
    "UMLS",
  ],
  authors: [
    { name: "Carlos Ramirez", url: "https://github.com/carlos-ramblox" },
  ],
  creator: "Carlos Ramirez",
  publisher: "University of Cambridge (PACE) Data Science programme",
  alternates: {
    canonical: "/",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
    },
  },
  openGraph: {
    type: "website",
    locale: "en_GB",
    url: SITE_URL,
    siteName: SITE_NAME,
    title: SITE_NAME,
    description: DESCRIPTION,
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_NAME,
    description: DESCRIPTION,
  },
  category: "research software",
};

const ldOrganization = {
  "@context": "https://schema.org",
  "@type": "Organization",
  name: SITE_NAME,
  url: SITE_URL,
  logo: `${SITE_URL}/logo.png`,
  parentOrganization: {
    "@type": "EducationalOrganization",
    name: "University of Cambridge",
    department: "PACE Data Science programme",
    url: "https://www.cam.ac.uk",
  },
};

const ldSoftwareSourceCode = {
  "@context": "https://schema.org",
  "@type": "SoftwareSourceCode",
  name: "clinicalcodes.uk",
  description: DESCRIPTION,
  url: SITE_URL,
  codeRepository: "https://github.com/carlos-ramblox/nice-clinical-codes",
  license: "https://www.apache.org/licenses/LICENSE-2.0",
  programmingLanguage: ["Python", "TypeScript"],
  runtimePlatform: ["Next.js", "FastAPI"],
  author: {
    "@type": "Person",
    name: "Carlos Ramirez",
    affiliation: {
      "@type": "EducationalOrganization",
      name: "University of Cambridge (PACE)",
    },
  },
  keywords:
    "clinical codes, codelist generation, SNOMED CT, ICD-10, OPCS-4, UMLS, retrieval-augmented generation, human-in-the-loop, electronic health records, NHS",
};

const ldWebSite = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  name: SITE_NAME,
  url: SITE_URL,
  inLanguage: "en-GB",
  description: DESCRIPTION,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${lora.variable} antialiased`}
    >
      <body className="min-h-screen flex flex-col bg-[#FBFAF8] text-[#0E0E0E] font-[family-name:var(--font-inter)]">
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify([ldOrganization, ldSoftwareSourceCode, ldWebSite]),
          }}
        />
        <header className="bg-white border-b border-gray-200 px-6 py-3">
          <div className="max-w-7xl mx-auto flex items-center justify-between">
            <div className="flex items-center gap-3">
              <img
                src="/logo.png"
                alt="Clinical Code Discovery"
                className="h-10 w-auto"
              />
              <span className="text-[10px] font-semibold bg-[#00436C] text-white px-1.5 py-0.5 rounded">
                Beta
              </span>
            </div>
          </div>
        </header>

        <Nav />

        <main className="flex-1">{children}</main>

        <footer className="mt-auto bg-[#00436C] text-white px-6 py-6">
          <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-3 text-xs">
            <span className="text-white/80">© 2026 Clinical Code Discovery</span>
            <div className="flex gap-4">
              <a href="/accessibility" className="hover:underline">Accessibility</a>
              <a href="/privacy" className="hover:underline">Privacy</a>
              <a href="/cookies" className="hover:underline">Cookies</a>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
