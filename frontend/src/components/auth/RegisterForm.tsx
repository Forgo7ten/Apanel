"use client";

import Link from "next/link";
import { FormEvent, useEffect, useLayoutEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { extractInviteToken, stripUrlFragment } from "@/lib/auth-logic.mjs";

import { useAuth } from "./AuthProvider";

const useIsomorphicLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

export function RegisterForm() {
  const router = useRouter();
  const { register, getErrorMessage } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [inviteToken, setInviteToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useIsomorphicLayoutEffect(() => {
    const hash = window.location.hash;
    const token = extractInviteToken(hash);

    setInviteToken(token);

    if (hash) {
      window.history.replaceState(window.history.state, "", stripUrlFragment(`${window.location.pathname}${window.location.search}${hash}`));
    }
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    if (password !== passwordConfirmation) {
      setError("两次输入的密码不一致。");
      return;
    }

    setSubmitting(true);

    try {
      await register(username.trim(), password, inviteToken);
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
        <label htmlFor="register-username" className="mb-2 block text-xs font-medium text-secondary">用户名</label>
        <input
          id="register-username"
          name="username"
          type="text"
          value={username}
          onChange={(event) => setUsername(event.target.value)}
          autoComplete="username"
          required
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "register-error" : undefined}
          className="h-11 w-full rounded-panel border border-line bg-card px-3.5 text-sm text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/25"
        />
      </div>

      <div>
        <label htmlFor="register-password" className="mb-2 block text-xs font-medium text-secondary">密码</label>
        <input
          id="register-password"
          name="password"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="new-password"
          required
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "register-error" : undefined}
          className="h-11 w-full rounded-panel border border-line bg-card px-3.5 text-sm text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/25"
        />
      </div>

      <div>
        <label htmlFor="register-password-confirmation" className="mb-2 block text-xs font-medium text-secondary">确认密码</label>
        <input
          id="register-password-confirmation"
          name="passwordConfirmation"
          type="password"
          value={passwordConfirmation}
          onChange={(event) => setPasswordConfirmation(event.target.value)}
          autoComplete="new-password"
          required
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "register-error" : undefined}
          className="h-11 w-full rounded-panel border border-line bg-card px-3.5 text-sm text-primary outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/25"
        />
      </div>

      <div className={`rounded-panel border px-3 py-2.5 text-xs leading-5 ${inviteToken ? "border-positive/25 bg-positive/10 text-positive" : "border-warning/25 bg-warning/10 text-warning"}`} role="status">
        <span className="font-medium">{inviteToken ? "邀请链接已识别" : "需要邀请链接"}</span>
        <span className="ml-2 text-muted">{inviteToken ? "token 已从地址栏移除，仅保留在本次注册请求内。" : "请使用管理员提供的 #token= 邀请链接。"}</span>
      </div>

      {error ? <p id="register-error" role="alert" className="rounded-panel border border-negative/25 bg-negative/10 px-3 py-2.5 text-xs leading-5 text-negative">{error}</p> : null}

      <button
        type="submit"
        disabled={submitting}
        className="inline-flex h-11 w-full items-center justify-center rounded-panel bg-brand px-4 text-sm font-semibold text-white shadow-panel transition hover:bg-brand/90 focus:outline-none focus:ring-2 focus:ring-brand/50 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {submitting ? "正在创建账号…" : "创建账号"}
      </button>

      <p className="text-center text-xs text-muted">
        已有账号？{" "}
        <Link href="/login" className="font-medium text-secondary underline decoration-line underline-offset-4 hover:text-primary">返回登录</Link>
      </p>
    </form>
  );
}
