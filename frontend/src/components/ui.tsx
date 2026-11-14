import { forwardRef, type ReactNode } from "react";
import { cn } from "../lib/utils";

export function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "primary" | "ghost" | "danger" }) {
  const { tone = "ghost", className, ...rest } = props;
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md border px-3 py-1.5 text-sm disabled:opacity-50",
        tone === "primary" && "border-accent bg-accent text-white",
        tone === "ghost" && "border-line bg-elevated hover:border-accent",
        tone === "danger" && "border-critical bg-critical/20 text-critical",
        className,
      )}
      {...rest}
    />
  );
}

export function Card({ title, extra, children, className }: { title?: string; extra?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-lg border border-line bg-surface", className)}>
      {title && (
        <header className="flex items-center justify-between border-b border-line px-4 py-2">
          <h2 className="text-sm font-semibold">{title}</h2>
          {extra}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Badge({ status, children }: { status?: string; children: ReactNode }) {
  const tone = status === "healthy" || status === "resolved" || status === "up" ? "text-healthy" : status === "warning" || status === "pending" ? "text-warning" : status === "critical" || status === "firing" || status === "down" ? "text-critical" : "text-info";
  return <span className={cn("inline-flex items-center gap-1 rounded border border-line bg-elevated px-2 py-0.5 text-xs", tone)}>{children}</span>;
}

export const Field = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Field(props, ref) {
  return <input ref={ref} {...props} className={cn("w-full rounded-md border border-line bg-elevated px-3 py-2 text-sm outline-none focus:border-accent", props.className)} />;
});

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn("rounded-md border border-line bg-elevated px-2 py-1.5 text-sm", props.className)} />;
}

export function StateBlock({ kind, message, onRetry }: { kind: "loading" | "empty" | "error" | "nodata"; message: string; onRetry?: () => void }) {
  return (
    <div className="flex min-h-32 flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-line bg-elevated/40 p-6 text-center text-sm text-muted">
      {kind === "loading" && <div className="h-16 w-full max-w-md animate-pulse rounded bg-line/50" />}
      <p>{message}</p>
      {onRetry && <Button onClick={onRetry}>Yenidən cəhd et</Button>}
    </div>
  );
}
