"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { useAuth } from "./AuthProvider";

export function LoginForm() {
  const router = useRouter();
  const { login, getErrorMessage } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      await login(username.trim(), password);
      router.replace("/watch");
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="space-y-5" onSubmit={handleSubmit}>
      <div>
        <label htmlFor="login-username" className="mb-2 block text-xs font-medium text-secondary">用户名</label>
        <input
          id="login-username"
          name="username"
          type="text"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          required
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "login-error" : undefined}
          className="h-11 w-full rounded-panel border border-line bg-card px-3.5 text-sm text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/25"
        />
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between gap-3">
          <label htmlFor="login-password" className="block text-xs font-medium text-secondary">密码</label>
          <span className="text-[11px] text-muted">仅用于当前登录请求</span>
        </div>
        <input
          id="login-password"
          name="password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          required
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "login-error" : undefined}
          className="h-11 w-full rounded-panel border border-line bg-card px-3.5 text-sm text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/25"
        />
      </div>

      {error ? <p id="login-error" role="alert" className="rounded-panel border border-negative/25 bg-negative/10 px-3 py-2.5 text-xs leading-5 text-negative">{error}</p> : null}

      <button
        type="submit"
        disabled={submitting}
        className="inline-flex h-11 w-full items-center justify-center rounded-panel bg-brand px-4 text-sm font-semibold text-white shadow-panel transition hover:bg-brand/90 focus:outline-none focus:ring-2 focus:ring-brand/50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {submitting ? "正在登录…" : "登录工作台"}
      </button>

      <p className="text-center text-xs text-muted">
        还没有账号？{" "}
        <Link href="/register" className="font-medium text-secondary underline decoration-line underline-offset-4 hover:text-primary">使用邀请链接注册</Link>
      </p>
    </form>
  );
}
