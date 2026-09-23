import Link from "next/link";

import { AuthFrame } from "@/components/auth/AuthFrame";
import { RegisterForm } from "@/components/auth/RegisterForm";

export const metadata = {
  title: "注册",
};

export default function RegisterPage() {
  return (
    <AuthFrame
      eyebrow="Invitation only"
      title="创建 Apanel 账号"
      description="使用管理员提供的邀请链接完成注册，注册成功后会直接进入工作台。"
      footer={
        <>
          已有账号？{" "}
          <Link href="/login" className="font-medium text-secondary underline decoration-line underline-offset-4 hover:text-primary">返回登录</Link>
        </>
      }
    >
      <RegisterForm />
    </AuthFrame>
  );
}
