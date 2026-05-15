"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserBadge } from "./UserBadge";

const links = [
  { href: "/", label: "Home" },
  { href: "/gallery", label: "Gallery" },
  { href: "/codelists", label: "Codelists" },
  { href: "/audit", label: "Audit Log" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <nav className="bg-[#00436C] text-white px-6">
      <div className="max-w-7xl mx-auto flex items-center text-sm">
        {links.map((l) => {
          const active =
            l.href === "/" ? pathname === "/" : pathname.startsWith(l.href);
          return (
            <Link
              key={l.href}
              href={l.href}
              aria-current={active ? "page" : undefined}
              className={`px-4 py-2.5 transition-colors hover:bg-[#005EA5] focus:outline-none focus:ring-2 focus:ring-[#005EA5] ${
                active ? "border-b-2 border-white -mb-px font-medium" : ""
              }`}
            >
              {l.label}
            </Link>
          );
        })}
        <div className="ml-auto">
          <UserBadge />
        </div>
      </div>
    </nav>
  );
}
