import type { Metadata } from "next";
import { Inter, Lora } from "next/font/google";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const lora = Lora({
  variable: "--font-lora",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "NICE Clinical Code List Generator",
  description:
    "Generate and validate clinical code lists (SNOMED CT, ICD-10) from public NHS data sources.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${lora.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-[#FBFAF8] text-[#0E0E0E] font-[family-name:var(--font-inter)]">
        <header className="bg-white border-b border-gray-200 px-6 py-3">
          <div className="max-w-7xl mx-auto flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-3xl font-black tracking-tight">NICE</span>
              <span className="text-xs text-gray-600 leading-tight hidden sm:block">
                National Institute for
                <br />
                Health and Care Excellence
              </span>
            </div>
            <div className="flex items-center gap-3">
              <div className="flex border border-black/80 bg-[#E9E9E9] overflow-hidden">
                <input
                  type="text"
                  placeholder="Search NICE..."
                  aria-label="Search NICE"
                  className="px-3 py-1.5 text-sm bg-transparent focus:outline-none w-48"
                  disabled
                />
                <button className="px-3 bg-[#404040] text-white" aria-label="Search" disabled>
                  <svg width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24" aria-hidden="true">
                    <circle cx="11" cy="11" r="8" />
                    <path d="m21 21-4.35-4.35" />
                  </svg>
                </button>
              </div>
            </div>
          </div>
        </header>

        <nav className="bg-[#00436C] text-white px-6">
          <div className="max-w-7xl mx-auto flex items-center gap-1 text-sm">
            <a href="/" className="px-4 py-2.5 hover:bg-[#005EA5] transition-colors">
              Home
            </a>
            <a href="#" className="px-4 py-2.5 hover:bg-[#005EA5] transition-colors">
              About
            </a>
            <a href="#" className="px-4 py-2.5 hover:bg-[#005EA5] transition-colors">
              Help
            </a>
            <div className="flex-1" />
            <span className="px-4 py-2.5 font-semibold">
              Clinical Code List Generator
            </span>
          </div>
        </nav>

        <main className="flex-1">{children}</main>

        <footer className="bg-white border-t border-gray-200 px-6 py-6">
          <div className="max-w-7xl mx-auto">
            <div className="flex justify-between items-start mb-4">
              <div>
                <span className="text-xl font-black">NICE</span>
                <span className="text-[10px] text-gray-500 ml-1">
                  National Institute for Health and Care Excellence
                </span>
              </div>
            </div>
            <div className="border-t border-gray-200 pt-3 flex justify-between text-xs text-gray-500">
              <span>Cambridge Data Science Career Accelerator — Group 3</span>
              <span>© NICE 2026. All rights reserved.</span>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
