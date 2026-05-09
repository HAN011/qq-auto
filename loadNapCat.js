const fs = require("fs");
const path = require("path");
const { pathToFileURL } = require("url");

function collectCandidates() {
  const envCandidates = [
    process.env.NAPCAT_MJS_PATH,
    process.env.NAPCAT_HOME ? path.join(process.env.NAPCAT_HOME, "napcat.mjs") : "",
  ].filter(Boolean);

  const localCandidates = [
    path.join(__dirname, "napcat.mjs"),
    path.join(process.cwd(), "napcat.mjs"),
  ];

  const home = process.env.HOME || process.env.USERPROFILE || "";
  const platformCandidates =
    process.platform === "win32"
      ? [
          "D:/tool/qq-auto/napcat.mjs",
          "C:/NapCat/napcat.mjs",
          home ? path.join(home, "NapCat", "napcat.mjs") : "",
          home ? path.join(home, ".napcat", "napcat.mjs") : "",
        ]
      : [
          "/opt/QQ/resources/app/app_launcher/napcat.mjs",
          "/opt/napcat/napcat.mjs",
          "/usr/local/lib/napcat/napcat.mjs",
          home ? path.join(home, ".local", "share", "NapCat", "napcat.mjs") : "",
          home ? path.join(home, ".napcat", "napcat.mjs") : "",
        ];

  return [...envCandidates, ...localCandidates, ...platformCandidates].filter(Boolean);
}

function resolveNapCatEntry() {
  for (const candidate of collectCandidates()) {
    if (fs.existsSync(candidate)) {
      return path.resolve(candidate);
    }
  }

  const searched = collectCandidates().map((item) => `- ${item}`).join("\n");
  throw new Error(
    [
      "未找到 napcat.mjs。",
      "请先在 Ubuntu 安装 NapCat，或设置环境变量 NAPCAT_MJS_PATH 指向 napcat.mjs。",
      "已搜索路径：",
      searched,
    ].join("\n"),
  );
}

(async () => {
  const entry = resolveNapCatEntry();
  await import(pathToFileURL(entry).href);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
