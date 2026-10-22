// 全局「配置写作渠道」弹层:任何生成请求撞上「未配置渠道」时的内联引导。
// 交互三原则:不跳页——弹窗就地出现,页面与已填内容原样保留;不重手——
// 快捷预设点一下自动填好协议/地址/模型,通常只需再填一个 API Key;不白配——
// 保存后由 http 层自动重放刚才失败的请求(pollJob 路径则提示再点一次生成)。
// 挂载:App.tsx 全站一个 <ProviderSetupHost />;命令式入口 openProviderSetup()。
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ProviderForm } from "../pages/settings/ProviderForm";
import { ProviderSetupCancelled } from "../http";

interface Settler {
  resolve: () => void;
  reject: (e: Error) => void;
}

let _listener: ((s: Settler) => void) | null = null;

/** 命令式打开「配置写作渠道」弹窗:保存成功 resolve;用户关掉 reject(ProviderSetupCancelled)。
 *  弹层未挂载(防御时序)同样按取消处理,调用方走原错误文案。 */
export function openProviderSetup(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!_listener) {
      reject(new ProviderSetupCancelled());
      return;
    }
    _listener({ resolve, reject });
  });
}

export function ProviderSetupHost({ onConfigured }: { onConfigured?: () => void }) {
  const [settler, setSettler] = useState<Settler | null>(null);

  useEffect(() => {
    _listener = (s) => setSettler(s);
    return () => { _listener = null; };
  }, []);

  function settle(ok: boolean) {
    if (!settler) return;
    if (ok) {
      onConfigured?.();
      settler.resolve();
    } else {
      settler.reject(new ProviderSetupCancelled());
    }
    setSettler(null);
  }

  return (
    <Dialog.Root
      open={!!settler}
      onOpenChange={(open) => { if (!open) settle(false); }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="dlg-overlay" />
        <Dialog.Content
          className="dlg-content dlg-provider-setup"
          onEscapeKeyDown={() => settle(false)}
          onInteractOutside={() => settle(false)}
        >
          <Dialog.Title className="dlg-title">配置写作渠道</Dialog.Title>
          <Dialog.Description className="dlg-body">
            填一个模型的 API Key 就能开始,通常 2 分钟;保存后刚才的操作会自动继续。
          </Dialog.Description>
          {/* settler 存在才渲染表单:每次打开都是干净表单,取消不残留半截输入 */}
          {settler && (
            <div style={{ marginTop: 14 }}>
              <ProviderForm hideHeader onSaved={() => settle(true)} onCancel={() => settle(false)} />
            </div>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
