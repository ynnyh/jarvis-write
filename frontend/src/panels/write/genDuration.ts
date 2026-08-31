// genDuration — 每章生成耗时记账与预估(P1 成本透明)。
// 后端 LlmUsage 只有用户/模型维度(无项目关联),真正的用户关切是"这一趟要跑多久":
// 前端按项目在 localStorage 记最近若干次的均值,发起生成/连写前给出预估。
// 首次无数据回落经验默认值;记录失败(隐私模式)静默忽略,预估仍可用。
const KEY = (pid: number) => `gen-dur-${pid}`;
// 滚动均值窗口:太久以前的速度不代表现在(模型/章长都会变),只看最近 5 次
const WINDOW = 5;
const DEFAULT_MIN = 5;
// 单章极值护栏:一次异常卡顿(如上游限流)不该把后续预估带歪
const MIN_SEC = 60, MAX_SEC = 60 * 30;

interface DurRec { n: number; avg: number }

function read(pid: number): DurRec | null {
  try {
    const raw = localStorage.getItem(KEY(pid));
    if (!raw) return null;
    const v = JSON.parse(raw) as DurRec;
    if (typeof v?.avg !== "number" || !(v.avg > 0)) return null;
    return v;
  } catch { return null; }
}

/** 记一次单章生成耗时(秒):滚动均值,n 只增不减(样本量参考)。 */
export function recordGenDuration(pid: number, sec: number): void {
  if (!(sec >= MIN_SEC && sec <= MAX_SEC)) return; // 异常值不入账
  try {
    const prev = read(pid);
    const n = Math.min((prev?.n ?? 0) + 1, WINDOW);
    const avg = prev ? prev.avg + (sec - prev.avg) / n : sec;
    localStorage.setItem(KEY(pid), JSON.stringify({ n, avg }));
  } catch { /* 隐私模式等,忽略 */ }
}

/** 当前每章预估分钟数(有历史用历史,无则回落默认)。 */
export function chapterEstimateMin(pid: number): number {
  const rec = read(pid);
  if (!rec || rec.n < 1) return DEFAULT_MIN;
  return Math.max(1, Math.round(rec.avg / 60));
}

/** 预估说人话:有历史报"上次一章约 X 分钟",无历史报"约 5 分钟左右"。 */
export function estimateText(pid: number): string {
  const rec = read(pid);
  return rec
    ? `上次一章约 ${Math.max(1, Math.round(rec.avg / 60))} 分钟`
    : `首次生成,约 ${DEFAULT_MIN} 分钟左右`;
}
