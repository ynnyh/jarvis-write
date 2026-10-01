// 原创漫剧独立入口；复用制作界面，作品与参考由服务端工作区边界隔离。
import AnimePage from "./AnimePage";

export default function OriginalDramaPage() {
  return <AnimePage workspace="original" />;
}
