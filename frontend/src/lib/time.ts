import { DateTime } from "luxon";

const ZONE = "Asia/Baku";

export function formatTime(value?: string | null) {
  if (!value) return "—";
  const parsed = DateTime.fromISO(value, { zone: "utc" });
  if (!parsed.isValid) return value;
  return parsed.setZone(ZONE).toFormat("dd.LL.yyyy HH:mm:ss");
}

export function rangeToIso(preset: string, customFrom?: string, customTo?: string) {
  const end = DateTime.utc().startOf("minute");
  const minutes: Record<string, number> = {
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "6h": 360,
    "24h": 1440,
    "7d": 1440 * 7,
    "30d": 1440 * 30,
  };
  if (preset === "custom" && customFrom && customTo) {
    return { start: customFrom, end: customTo };
  }
  const start = end.minus({ minutes: minutes[preset] ?? 360 });
  return { start: start.toISO(), end: end.toISO() };
}
