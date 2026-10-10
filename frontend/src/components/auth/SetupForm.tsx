"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError } from "@/lib/api-errors";
import { submitSetup } from "@/lib/setup";

const fieldClass = "mt-2 h-11 w-full rounded-panel border border-line bg-card px-3.5 text-sm text-primary outline-none focus:border-brand focus:ring-2 focus:ring-brand/25";

export function SetupForm() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [username, setUsername] = useState("admin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password !== confirmation) {
      setError("两次输入的密码不一致。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await submitSetup({ token: token.trim(), username: username.trim(), email: email.trim(), password });
      setToken("");
      setPassword("");
      setConfirmation("");
      router.replace("/login");
    } catch (cause) {
      const code = cause instanceof ApiError ? cause.code : "NETWORK_ERROR";
      setError(({
        INVALID_SETUP_TOKEN: "初始化令牌错误，请核对项目根目录中的文件。",
        SETUP_ALREADY_COMPLETED: "系统已完成初始化，请直接登录。",
        ADMIN_ALREADY_EXISTS: "管理员已存在，无法再次初始化。",
        SETUP_RATE_LIMITED: "尝试次数过多，请稍后重试。",
        SETUP_NOT_READY: "初始化任务尚未准备好，请稍后重试。",
        INVALID_REQUEST: "输入不符合要求，请检查用户名、邮箱、密码与令牌。",
        NETWORK_ERROR: "无法连接后端，请检查网络和服务状态。",
      } as Record<string, string>)[code] ?? "初始化失败，请检查输入后重试。");
    } finally {
      setBusy(false);
    }
  }

  return <form className="space-y-5" onSubmit={handleSubmit}>
    <p className="rounded-panel border border-line bg-card p-3 text-xs leading-5 text-secondary">
      在部署服务器的 Apanel 项目目录执行 <code>cat .apanel-setup-token</code>，
      将文件内的一次性令牌复制到下面。请勿分享令牌。
    </p>
    <label className="block text-xs font-medium text-secondary">
      一次性初始化令牌
      <input className={fieldClass} type="password" value={token} onChange={(event) => setToken(event.target.value)}
        autoComplete="off" spellCheck={false} required minLength={77} maxLength={77} />
    </label>
    <label className="block text-xs font-medium text-secondary">
      管理员用户名
      <input className={fieldClass} value={username} onChange={(event) => setUsername(event.target.value)}
        autoComplete="username" required minLength={3} maxLength={64} />
    </label>
    <label className="block text-xs font-medium text-secondary">
      管理员邮箱
      <input className={fieldClass} type="email" value={email} onChange={(event) => setEmail(event.target.value)}
        autoComplete="email" required maxLength={320} />
    </label>
    <label className="block text-xs font-medium text-secondary">
      设置密码（至少 8 位）
      <input className={fieldClass} type="password" value={password} onChange={(event) => setPassword(event.target.value)}
        autoComplete="new-password" required minLength={8} maxLength={256} />
    </label>
    <label className="block text-xs font-medium text-secondary">
      确认密码
      <input className={fieldClass} type="password" value={confirmation}
        onChange={(event) => setConfirmation(event.target.value)}
        autoComplete="new-password" required minLength={8} maxLength={256} />
    </label>
    {error ? <p role="alert" className="rounded-panel border border-negative/25 bg-negative/10 p-3 text-xs text-negative">{error}</p> : null}
    <button type="submit" disabled={busy} className="h-11 w-full rounded-panel bg-brand px-4 text-sm font-semibold text-white disabled:opacity-50">
      {busy ? "正在完成初始化…" : "创建管理员并完成初始化"}
    </button>
  </form>;
}
