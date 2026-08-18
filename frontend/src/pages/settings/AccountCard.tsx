// 账号卡:修改登录密码。拆自 SettingsPage.tsx。
// 仅 server 多用户模式渲染;local(桌面单机)免登录,后端也明确拒绝改密。
import { useEffect, useState } from "react";
import { api } from "../../api";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";

export function AccountCard() {
  // null=尚未探测到运行模式(先不渲染,避免桌面版闪一下)
  const [isLocal, setIsLocal] = useState<boolean | null>(null);
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.mode()
      .then((m) => setIsLocal(m.is_local))
      .catch(() => setIsLocal(false)); // 探测失败按 server 处理
  }, []);

  // 即时校验:输入过程中即提示,不满足则禁用提交(长度规则与注册一致:至少 6 位)
  const fieldErr =
    newPw && newPw.length < 6 ? "新密码至少 6 位"
    : confirmPw && confirmPw !== newPw ? "两次输入的新密码不一致"
    : "";
  const canSubmit = !!oldPw && newPw.length >= 6 && confirmPw === newPw && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    try {
      await api.changePassword(oldPw, newPw);
      toast.ok("密码已修改", "下次登录请使用新密码");
      setOldPw(""); setNewPw(""); setConfirmPw("");
    } catch (e) {
      toast.err("修改失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  if (isLocal === null || isLocal) return null;

  return (
    <div className="card">
      <div className="card-head"><h2>账号</h2></div>
      <p className="card-desc">
        修改登录密码,需先验证旧密码。修改后已登录的其他设备不会被强制退出。
      </p>
      <form onSubmit={submit}>
        <label className="fl">旧密码</label>
        <input
          type="password"
          value={oldPw}
          autoComplete="current-password"
          onChange={(e) => setOldPw(e.target.value)}
        />
        <div className="fld-row">
          <div className="fld">
            <label className="fl">新密码</label>
            <input
              type="password"
              value={newPw}
              autoComplete="new-password"
              onChange={(e) => setNewPw(e.target.value)}
              placeholder="至少 6 位"
            />
          </div>
          <div className="fld">
            <label className="fl">确认新密码</label>
            <input
              type="password"
              value={confirmPw}
              autoComplete="new-password"
              onChange={(e) => setConfirmPw(e.target.value)}
            />
          </div>
        </div>
        {fieldErr && <div className="test-line err">{fieldErr}</div>}
        <div className="provider-actions">
          <button className="primary btn-sm" type="submit" disabled={!canSubmit}>
            {busy && <span className="spin" />}修改密码
          </button>
        </div>
      </form>
    </div>
  );
}
