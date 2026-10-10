import { AuthFrame } from "@/components/auth/AuthFrame";
import { SetupForm } from "@/components/auth/SetupForm";
import { SetupGuard } from "@/components/auth/SetupGuard";

export const metadata = { title: "首次初始化" };

export default function SetupPage() {
  return <SetupGuard setupPage>
    <AuthFrame eyebrow="First-run setup" title="初始化 Apanel"
      description="使用服务器上的一次性令牌设置首个管理员。该操作只能成功一次。"
      footer="初始化完成后令牌永久失效，可自行删除服务器上的令牌文件。">
      <SetupForm />
    </AuthFrame>
  </SetupGuard>;
}
