import type { SVGProps } from "react";

import type { NavItem } from "@/lib/navigation";

type IconName = NavItem["icon"];

type NavIconProps = SVGProps<SVGSVGElement> & {
  name: IconName;
};

export function NavIcon({ name, ...props }: NavIconProps) {
  const commonProps = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 1.7,
    viewBox: "0 0 24 24",
    "aria-hidden": true,
    ...props,
  };

  if (name === "monitor") {
    return (
      <svg {...commonProps}>
        <rect x="3" y="4" width="18" height="14" rx="2" />
        <path d="M8 21h8M12 18v3M7 14l3-3 2 2 4-5" />
      </svg>
    );
  }

  if (name === "bell") {
    return (
      <svg {...commonProps}>
        <path d="M18 9a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4" />
      </svg>
    );
  }

  return (
    <svg {...commonProps}>
      <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.64 5.64l1.42 1.42M16.94 16.94l1.42 1.42M18.36 5.64l-1.42 1.42M7.06 16.94l-1.42 1.42" />
      <circle cx="12" cy="12" r="3.5" />
    </svg>
  );
}
