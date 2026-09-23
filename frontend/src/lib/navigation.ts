export const navItems = [
  {
    href: "/watch",
    label: "股票监控",
    description: "指标状态与变化",
    icon: "monitor",
  },
  {
    href: "/notifications",
    label: "通知",
    description: "规则与通知记录",
    icon: "bell",
  },
  {
    href: "/settings",
    label: "设置",
    description: "数据与工作台偏好",
    icon: "settings",
  },
] as const;

export type NavItem = (typeof navItems)[number];

export function getPageMeta(pathname: string): NavItem {
  return navItems.find(
    (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
  ) ?? navItems[0];
}
