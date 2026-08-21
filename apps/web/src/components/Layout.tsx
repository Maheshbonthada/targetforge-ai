import type { ReactNode } from "react";
import { Link } from "react-router-dom";

const ENV_LABEL = (import.meta.env.VITE_ENVIRONMENT as string | undefined) ?? "local";

export function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/" className="brand">
          <span className="mark">TF</span>
          TargetForge
        </Link>
        <span className="env-pill">{ENV_LABEL}</span>
      </header>
      <main className="app-main">{children}</main>
    </div>
  );
}
