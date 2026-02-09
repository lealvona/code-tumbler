"use client";

import { useEffect, useState } from "react";
import type { ProviderInfo } from "@/lib/types";
import { api } from "@/lib/api";
import { ProviderList } from "@/features/models/provider-list";

export default function ModelsPage() {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);

  useEffect(() => {
    api.listProviders().then(setProviders).catch(console.error);
  }, []);

  return (
    <div className="p-6 space-y-6">
      <h2 className="text-2xl font-bold tracking-tight">Models & Providers</h2>
      <p className="text-sm text-muted-foreground">
        Configured LLM providers and their models.
      </p>
      <ProviderList providers={providers} />
    </div>
  );
}
