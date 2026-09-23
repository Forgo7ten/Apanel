import Link from "next/link";

import { AuthFrame } from "@/components/auth/AuthFrame";
import { LoginForm } from "@/components/auth/LoginForm";

export const metadata = {
  title: "登录",
};

export default function LoginPage() {
  return (
    <AuthFrame
      eyebrow="Secure access"
      title="登录 Apanel"
      description="使用你的账号进入指标监控工作台。"
      footer={
        <>
          账号由管理员邀请。需要注册？{" "}
          <Link href="/register" className="font-medium text-secondary underline decoration-line underline-offset-4 hover:text-primary">使用邀请链接</Link>
        </>
      }
    >
      <LoginForm />
    </AuthFrame>
  );
}
