// 字符级 diff(「正文即界面」P3,docs/10 §6/§9):不引第三方库,手写 LCS。
//   diffChars —— 两段短文本的字符级删增,中文按码点切(Array.from 处理代理对),
//     段落一般几十~几百字,O(n·m) DP 无压力;乘积超上限退化为「整删+整增」兜底不炸内存。
//   diffParagraphs —— ③④档整章验收用:段落级 LCS 对齐(整段作 token)锚定未变段落,
//     变动区成对做字符级 diff,产出逐段 same/changed/added/removed 序列。
// 两者皆纯函数,好测;渲染沿用校对卡的 .diff-old/.diff-new(见 DiffText.tsx 与 styles.css)。
import { splitParas } from "../../components/Reader";

export type DiffType = "same" | "del" | "ins";
export interface DiffOp { type: DiffType; text: string }

// DP 表格子数上限(约 400 万);段内 diff 远达不到,超限(极罕见的整段超长)退化兜底
const MAX_CELLS = 4_000_000;

/** 合并同类相邻字符,减少渲染碎片:相邻同类型 op 文本相接,否则新起一段 */
function push(ops: DiffOp[], type: DiffType, ch: string): void {
  const last = ops[ops.length - 1];
  if (last && last.type === type) last.text += ch;
  else ops.push({ type, text: ch });
}

/** 字符级 diff:返回把 a 变成 b 的 same/del/ins 操作序列(按码点,合并同类相邻)。 */
export function diffChars(a: string, b: string): DiffOp[] {
  if (a === b) return a ? [{ type: "same", text: a }] : [];
  const A = Array.from(a);
  const B = Array.from(b);
  if (A.length === 0) return [{ type: "ins", text: b }];
  if (B.length === 0) return [{ type: "del", text: a }];
  if (A.length * B.length > MAX_CELLS) {
    return [{ type: "del", text: a }, { type: "ins", text: b }];
  }
  const n = A.length, m = B.length;
  // dp[i][j] = A[i:] 与 B[j:] 的最长公共子序列长度(倒序填表,便于正向回溯)
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = A[i] === B[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops: DiffOp[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j]) { push(ops, "same", A[i]); i++; j++; }
    // 删优先于增(dp 相等时倾向先消耗旧串),让 diff 稳定可预期
    else if (dp[i + 1][j] >= dp[i][j + 1]) { push(ops, "del", A[i]); i++; }
    else { push(ops, "ins", B[j]); j++; }
  }
  while (i < n) { push(ops, "del", A[i]); i++; }
  while (j < m) { push(ops, "ins", B[j]); j++; }
  return ops;
}

export type ParaStatus = "same" | "changed" | "added" | "removed";
export interface ParaDiff {
  status: ParaStatus;
  oldIdx: number | null;   // 旧正文里的段号(added 为 null)
  newIdx: number | null;   // 新正文里的段号(removed 为 null)
  oldText: string;
  newText: string;
  ops: DiffOp[] | null;    // changed 时的字符级 diff;其余为 null
}

/** 段落级对齐:整段作 token 做 LCS,返回旧/新段落交错的对齐步骤 */
function alignParas(O: string[], N: string[]): { o: number | null; n: number | null }[] {
  const n = O.length, m = N.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = O[i] === N[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const steps: { o: number | null; n: number | null }[] = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (O[i] === N[j]) { steps.push({ o: i, n: j }); i++; j++; }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { steps.push({ o: i, n: null }); i++; }
    else { steps.push({ o: null, n: j }); j++; }
  }
  while (i < n) { steps.push({ o: i, n: null }); i++; }
  while (j < m) { steps.push({ o: null, n: j }); j++; }
  return steps;
}

/** 整章逐段 diff:锚定未变段落,变动区里旧/新段落成对配成 changed(字符级 diff),
 *  多出来的旧段=removed、新段=added。常见「同段数、改了几段」场景对齐精准。 */
export function diffParagraphs(oldText: string, newText: string): ParaDiff[] {
  const O = splitParas(oldText);
  const N = splitParas(newText);
  const steps = alignParas(O, N);
  const out: ParaDiff[] = [];
  // 把连续的「只旧」「只新」步骤攒成一段变动区,再成对配成 changed
  let delBuf: number[] = [];
  let insBuf: number[] = [];
  const flush = () => {
    const k = Math.min(delBuf.length, insBuf.length);
    for (let t = 0; t < k; t++) {
      const oi = delBuf[t], ni = insBuf[t];
      out.push({
        status: "changed", oldIdx: oi, newIdx: ni,
        oldText: O[oi], newText: N[ni], ops: diffChars(O[oi], N[ni]),
      });
    }
    for (let t = k; t < delBuf.length; t++) {
      out.push({ status: "removed", oldIdx: delBuf[t], newIdx: null, oldText: O[delBuf[t]], newText: "", ops: null });
    }
    for (let t = k; t < insBuf.length; t++) {
      out.push({ status: "added", oldIdx: null, newIdx: insBuf[t], oldText: "", newText: N[insBuf[t]], ops: null });
    }
    delBuf = []; insBuf = [];
  };
  for (const s of steps) {
    if (s.o !== null && s.n !== null) {
      flush();
      out.push({ status: "same", oldIdx: s.o, newIdx: s.n, oldText: O[s.o], newText: N[s.n], ops: null });
    } else if (s.o !== null) {
      delBuf.push(s.o);
    } else if (s.n !== null) {
      insBuf.push(s.n);
    }
  }
  flush();
  return out;
}
