// 应用锁卡:启动密码设/改/移除(仅 local 桌面单机模式)。拆自 SettingsPage.tsx。
// 休闲锁:启动 app 需输密码才能进主界面,防家人/同事随手翻开,不抵御直接读数据文件。
// server 模式有自己的账号体系,不渲染(与 AccountCard 互斥)。
import { useEffect, useState } from "react";
import { api } from "../../api";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";

export function AppLockCard() {
  // null=非 local 模式或尚未探测到(不渲染)
  const [hasLock, setHasLock] = useState<boolean | null>(null);
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [removePw, setRemovePw] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.mode()
      .then((m) => { if (m.is_local) setHasLock(m.has_lock); })
      .catch(() => { /* 探测失败按 server 处理:不渲染 */ });
  }, []);

  // 即时校验:输入过程中即提示,不满足则禁用提交(长度规则与注册一致:至少 6 位)
  const fieldErr =
    newPw && newPw.length < 6 ? "锁密码至少 6 位"
    : confirmPw && confirmPw !== newPw ? "两次输入的密码不一致"
    : "";
  const canSubmit =
    (hasLock === false || !!oldPw) && newPw.length >= 6 && confirmPw === newPw && !busy;

  async function submitSet(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true);
    try {
      await api.appLockSet(newPw, hasLock ? oldPw : undefined);
      toast.ok(hasLock ? "锁密码已修改" : "应用锁已开启", "下次启动应用时需输入密码");
      setHasLock(true);
      setOldPw(""); setNewPw(""); setConfirmPw("");
    } catch (e) {
      toast.err(hasLock ? "修改失败" : "设置失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function submitRemove(e: React.FormEvent) {
    e.preventDefault();
    if (!removePw || busy) return;
    setBusy(true);
    try {
      await api.appLockRemove(removePw);
      toast.ok("应用锁已移除", "下次启动应用不再需要密码");
      setHasLock(false);
      setRemovePw("");
    } catch (e) {
      toast.err("移除失败", errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  if (hasLock === null) return null;

  return (
    <div className="card">
      <div className="card-head"><h2>应用锁</h2></div>
      <p className="card-desc">
        开启后每次启动应用都需输入密码才能进入,防止他人随手翻开你的作品。
        (防君子不防黑客:挡不住直接读取本机数据文件。)
      </p>

      <form onSubmit={submitSet}>
        {hasLock && (
          <>
            <label className="fl">旧密码</label>
            <input
              type="password"
              value={oldPw}
              autoComplete="current-password"
              onChange={(e) => setOldPw(e.target.value)}
            />
          </>
        )}
        <div className="fld-row">
          <div className="fld">
            <label className="fl">{hasLock ? "新锁密码" : "锁密码"}</label>
            <input
              type="password"
              value={newPw}
              autoComplete="new-password"
              onChange={(e) => setNewPw(e.target.value)}
              placeholder="至少 6 位"
            />
          </div>
          <div className="fld">
            <label className="fl">{hasLock ? "确认新密码" : "确认密码"}</label>
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
            {busy && <span className="spin" />}{hasLock ? "修改锁密码" : "设置应用锁"}
          </button>
        </div>
      </form>

      {hasLock && (
        <form onSubmit={submitRemove}>
          <label className="fl">移除应用锁(输入当前锁密码确认)</label>
          <input
            type="password"
            value={removePw}
            autoComplete="current-password"
            onChange={(e) => setRemovePw(e.target.value)}
          />
          <div className="provider-actions">
            <button className="btn-sm danger" type="submit" disabled={!removePw || busy}>
              移除应用锁
            </button>
          </div>
          <p className="card-desc">
            忘记锁密码?在锁屏页点「忘记密码?」可重置(将移除应用锁,数据不受影响)。
          </p>
        </form>
      )}
    </div>
  );
}
