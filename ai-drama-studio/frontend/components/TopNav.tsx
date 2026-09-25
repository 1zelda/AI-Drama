"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

type NavItem = { href: string; label: string; icon?: string };

const NAV: NavItem[] = [
  { href: "/", label: "首页", icon: "🏠" },
  { href: "/make", label: "一键成片", icon: "⚡" },
  { href: "/history", label: "成片库", icon: "📺" },
  { href: "/studio", label: "剧情工坊", icon: "📖" },
  { href: "/pipeline", label: "流水线", icon: "🎬" },
  { href: "/assets", label: "资产库", icon: "🗂️" },
  { href: "/audio", label: "配音音乐", icon: "🎙️" },
  { href: "/export", label: "剪映导出", icon: "🎞️" },
  { href: "/routing", label: "镜头路由", icon: "🧭" },
  { href: "/settings", label: "设置", icon: "⚙️" },
];

/**
 * 全站统一导航。以前每个页面自己手写一排 Link，入口还常常不一样
 * （有的页面漏了资产库、有的漏了设置），现在统一从这里出。
 */
export default function TopNav({
  title,
  subtitle,
  actions,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  const pathname = usePathname();

  return (
    <header style={{ borderBottom: "1px solid #232323", background: "#0b0b0b" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "0.7rem 1.25rem",
          flexWrap: "wrap",
        }}
      >
        <Link
          href="/"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            textDecoration: "none",
            color: "#fff",
            flexShrink: 0,
          }}
        >
          <span
            style={{
              width: 30,
              height: 30,
              borderRadius: 9,
              background: "linear-gradient(135deg,#6366f1,#8b5cf6)",
              display: "grid",
              placeItems: "center",
              fontSize: 15,
            }}
          >
            🎬
          </span>
          <span style={{ fontWeight: 700, fontSize: "0.98rem" }}>AI Drama Studio</span>
        </Link>

        {title && (
          <>
            <span style={{ color: "#333" }}>/</span>
            <strong style={{ fontSize: "0.95rem", color: "#e5e7eb" }}>{title}</strong>
          </>
        )}
        {subtitle && (
          <span style={{ fontSize: "0.75rem", color: "#666" }}>{subtitle}</span>
        )}

        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          {actions}
        </div>
      </div>

      <nav
        style={{
          display: "flex",
          gap: 4,
          padding: "0 1.25rem 0.5rem",
          overflowX: "auto",
          flexWrap: "wrap",
        }}
      >
        {NAV.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
                padding: "0.32rem 0.7rem",
                borderRadius: 999,
                fontSize: "0.78rem",
                textDecoration: "none",
                whiteSpace: "nowrap",
                color: active ? "#c7d2fe" : "#8b8b8b",
                background: active ? "#6366f126" : "transparent",
                border: `1px solid ${active ? "#6366f159" : "transparent"}`,
                transition: "all .15s",
              }}
            >
              <span style={{ fontSize: "0.85rem" }}>{item.icon}</span>
              {item.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
