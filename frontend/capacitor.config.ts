import type { CapacitorConfig } from '@capacitor/cli';

// 安卓壳(Capacitor):**热更新模式**——WebView 直接加载官方服务器上的前端界面,
// 服务器发新版 App 自动跟上,用户无需重装 APK;只有原生层(Capacitor/插件)变化
// 才需要重新打包分发。dist-app 本地产物仅作 cap sync 的工程依赖,运行时不加载。
// 登录页可改服务器地址(存 localStorage),apiBase 会把请求指过去。
const OFFICIAL_APP_ENTRY = 'http://111.228.10.230:8080/app/';

const config: CapacitorConfig = {
  appId: 'com.ynnyh.jarviswrite',
  appName: 'jarvis-write',
  webDir: 'dist-app',
  server: {
    url: OFFICIAL_APP_ENTRY,
    // 官方服务器现为 http 直连(IP+端口),必须允许明文;套 https 后此开关无副作用
    cleartext: true,
    androidScheme: 'https',
  },
};

export default config;
