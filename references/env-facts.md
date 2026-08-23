# AutoYY 项目关键环境事实（2026-08）

内存条目已精简,完整事实存此。修改/维护 AutoYY 时先读本文件。

## 项目位置与同步

- 项目根目录：`D:\@kaifa\AutoYY`
- Git 仓库：`DaBaoAgent/AutoYY`（GitHub,HTTPS 被墙,走 SSH 443 端口）
- 本机 codex + hermes 均通过 **junction 指向**同一份源码 —— 任意一端 commit + push 即多端同步；另一台机器只 pull 即可
- 注意：junction 方案下不要用删除目录的方式重装,会破坏链接

## 文案标准

- 口播文案：4500-5500 字 / 纯中文 / 去 AI 味
- 具体标准见本技能 `references/content-style.md`（或 creative 下文案类技能）

## 已知坑

- `read_file` 对中文 txt 会误判为二进制 → 用 python 读（`open(..., encoding='utf-8')`）
- 委托子代理输出路径必须写 E 盘（曾误写 `D:\@油管二创素材` 建出空骨架需清理）
- delegation 已放宽：1800s / 150 轮

## 工程化增强环境事实（2026-08-23 起）

- **yt-dlp 2026+ 解析 YouTube 必须 JS runtime**：本机 node 在 `C:\Program Files\nodejs`，`download_from_manifest.ps1` 自动探测 node/deno 并传 `--js-runtimes`；缺 node 会报 "No supported JavaScript runtime"
- **aria2c 1.37.0 已装**（winget, `aria2.aria2`），ps1 `-UseAria2` 启用 8 连接；yt-dlp 内置 `--concurrent-fragments 8`
- **ffmpeg 自动探测**：ps1/transcribe.py/validate_subtitles.py 均支持 `--ffmpeg-location`，ps1 还自动探测 `~/ffmpeg/*/bin`
- **transcribe.py 默认后端 faster-whisper medium（CPU int8）**，`--backend funasr` 需先 `pip install funasr`（中文效果更好）；模型经 HF_ENDPOINT=hf-mirror.com 下载，无需手动配置
- **发布信息进化库** `assets/peer-hit-library.csv`：`peer_hit_library.py --patterns` 看钩子模式排名，`--add/--import` 回流数据，库随 git 同步
- ps1 状态机验证过：manifest `status=ready` 或 `下载状态.csv` complete 且文件在 → 跳过；错误原因入 status 列
