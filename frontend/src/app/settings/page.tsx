"use client";

import { ConfigForm } from "@/features/settings/config-form";

export default function SettingsPage() {
  return (
    <div className="p-6 space-y-6">
      <h2 className="text-2xl font-bold tracking-tight">Settings</h2>
      <p className="text-sm text-muted-foreground">
        Configure providers, agent assignments, and tumbler behavior.
      </p>
      <ConfigForm />
    </div>
  );
}
