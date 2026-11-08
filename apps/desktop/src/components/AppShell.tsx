import type { ReactNode } from "react";

type AppShellProps = {
  sidebar: ReactNode;
  children: ReactNode;
};

export function AppShell({ sidebar, children }: AppShellProps) {
  return (
    <div className="app-frame">
      {sidebar}
      <main className="workspace" id="workspace-main">{children}</main>
    </div>
  );
}
