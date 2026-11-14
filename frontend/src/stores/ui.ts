import { create } from "zustand";

type Theme = "dark" | "light";
type Mode = "demo" | "real" | "mixed";

interface UiState {
  token: string | null;
  user: { email: string; full_name: string; role: string } | null;
  theme: Theme;
  mode: Mode;
  preset: string;
  tenantId: string;
  live: boolean;
  setToken: (token: string | null) => void;
  setUser: (user: UiState["user"]) => void;
  setTheme: (theme: Theme) => void;
  setMode: (mode: Mode) => void;
  setPreset: (preset: string) => void;
  setTenantId: (tenantId: string) => void;
  setLive: (live: boolean) => void;
}

export const useUi = create<UiState>((set) => ({
  token: null,
  user: null,
  theme: "dark",
  mode: "demo",
  preset: "15m",
  tenantId: "",
  live: false,
  setToken: (token) => set({ token }),
  setUser: (user) => set({ user }),
  setTheme: (theme) => set({ theme }),
  setMode: (mode) => set({ mode }),
  setPreset: (preset) => set({ preset }),
  setTenantId: (tenantId) => set({ tenantId }),
  setLive: (live) => set({ live }),
}));
