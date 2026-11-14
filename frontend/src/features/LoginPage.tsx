import { useForm } from "react-hook-form";
import { z } from "zod";
import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useUi } from "../stores/ui";
import { Button, Card, Field } from "../components/ui";
import { useState } from "react";

const schema = z.object({ email: z.string().email(), password: z.string().min(8) });

export function LoginPage() {
  const navigate = useNavigate();
  const setToken = useUi((state) => state.setToken);
  const setUser = useUi((state) => state.setUser);
  const [error, setError] = useState("");
  const form = useForm<{ email: string; password: string }>({ resolver: zodResolver(schema) });
  return (
    <div className="flex min-h-full items-center justify-center">
      <Card title="HMC Observability" className="w-full max-w-md">
        <form
          className="space-y-3"
          onSubmit={form.handleSubmit(async (values) => {
            setError("");
            try {
              const result = await api<{ access_token: string; user: { email: string; full_name: string; role: string } }>("/api/v1/auth/login", { method: "POST", body: JSON.stringify(values) });
              setToken(result.access_token);
              setUser(result.user);
              navigate("/");
            } catch (caught) {
              setError(caught instanceof Error ? caught.message : "Giriş alınmadı");
            }
          })}
        >
          <Field placeholder="E-poçt" {...form.register("email")} />
          <Field type="password" placeholder="Şifrə" {...form.register("password")} />
          {error && <p className="text-sm text-critical">{error}</p>}
          <Button tone="primary" type="submit">Daxil ol</Button>
        </form>
      </Card>
    </div>
  );
}
