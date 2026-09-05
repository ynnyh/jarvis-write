import type { CapacitorConfig } from '@capacitor/cli';

// 安卓壳(Capacitor)配置:壳内打包的是前端产物(dist-app,base=./),
// 运行时通过登录页填的服务器地址(Capacitor → localStorage jarvis_server)
// 连到用户自己的 jarvis-write 服务;壳本身不内置后端。
const config: CapacitorConfig = {
  appId: 'com.ynnyh.jarviswrite',
  appName: 'jarvis-write',
  webDir: 'dist-app',
  server: {
    // 安卓 WebView 里走 https(localhost 默认即 https),允许明文仅限自填 http 地址时由系统提示
    androidScheme: 'https',
  },
};

export default config;
