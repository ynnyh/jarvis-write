// 品牌 logo「故事线」:三条章节灰线之间,一条金色故事线贯穿不断,线头一颗墨点。
// 章节在走,线不断——长程一致性的视觉化;与官网(docs/.vitepress/theme)同源同款。
export default function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      style={{ display: "block", borderRadius: Math.round(size * 0.22), flex: "none" }}
    >
      <rect x="2" y="2" width="60" height="60" rx="15" fill="#33454e" />
      <line x1="15" y1="21.5" x2="34" y2="21.5" stroke="#ece9e0" strokeWidth="4" strokeLinecap="round" opacity="0.5" />
      <line x1="15" y1="32" x2="41" y2="32" stroke="#ece9e0" strokeWidth="4" strokeLinecap="round" opacity="0.75" />
      <line x1="15" y1="42.5" x2="29" y2="42.5" stroke="#ece9e0" strokeWidth="4" strokeLinecap="round" opacity="0.5" />
      <path d="M12 51 C 27 45, 25 25, 51 14.5" stroke="#d4ab5f" strokeWidth="4.5" strokeLinecap="round" />
      <circle cx="51" cy="14.5" r="3.6" fill="#d4ab5f" />
    </svg>
  );
}
