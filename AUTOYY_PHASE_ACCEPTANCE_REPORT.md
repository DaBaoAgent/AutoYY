# AutoYY Phase 0–8 最终验收报告

- 验收日期：2026-10-01
- 项目路径：`<local-checkout>`
- 当前版本：`autoyy 0.1.0`
- 目标：将 AutoYY 收敛为可审计、可恢复、批量不降质、Windows 兼容、适合 Hermes / Workbuddy / Codex 使用的开源项目。

## 最终结论

本轮计划内的 Phase 0–8 已完成实现与回归验收。核心结果不是“文件能生成”，而是把下载、字幕、口播、发布信息、封面、状态恢复和最终交付统一到可机器判定的 Gate 上。

最终 release gate 结果：

- `86 tests collected`，完整测试集通过。
- GitHub Actions P0 hardening run `36798612582`：Python 3.11、Python 3.12、wheel-smoke 三个 job 全部通过。
- 远端 Python 3.11 / 3.12 coverage 均高于 `85%` hard gate；3.12 实测 `86.13%`。
- Python compile、Ruff、PowerShell 5.1 语法、PowerShell 7 wrapper integration、`git diff --check` 与 fresh-wheel install smoke 均通过。
- `strategy.fail-fast=false`，任一 Python matrix 失败不会取消另一版本的验收证据。
## Phase 0 — 基线与失败语义

已完成：

- 建立统一 `0 / 1 / 2` 退出码契约：成功 / 有失败或未完成项 / 输入或环境不可用。
- 空目录、零字节文件、坏 manifest、路径逃逸、损坏 state 不再被静默判成功。
- Windows-first 环境事实集中到 `references/env-facts.md`，代码不再依赖开发者用户名或私有样本目录。
- 新增仓库卫生测试，防止 BOM、连续 `question-mark` 编码损坏和私有路径重新进入源码。

验收：PASS。

## Phase 1 — 兼容入口与安全边界

已完成：

- `scripts/` 保留兼容入口，但核心业务规则下沉到 `src/autoyy/`。
- manifest 的 `folder_name` 强制限制在 output root 下，拒绝绝对路径和 `..` 逃逸。
- 下载只支持经过验证的 YouTube URL 形态；Cookie / proxy 均保持显式参数，不写入状态。
- 下载 ready 判断不再信任旧 status 标签，必须依据当前磁盘文件重新验证。

验收：PASS。
## Phase 2 — 公共核心与统一 CLI

已完成：

- 新增 `src/autoyy/`，集中实现 config、paths、manifest、media、subtitles、publication、peer、state、download、deliverables、voiceover、doctor、CLI。
- 新增 `python -m autoyy` / `autoyy` 稳定命令面，兼容脚本只做代理或薄封装。
- 统一 validators，避免同一规则在多个脚本中各写一份并逐渐漂移。
- `pyproject.toml` 提供开发、cover-local、ASR 可选依赖与 console script。

验收：PASS。

## Phase 3 — 下载与字幕工程化

已完成：

- 下载并行从 PowerShell 版本差异中抽离，统一使用 Python worker pool；`-Parallel` 在 PS5.1 / PS7 行为一致。
- 下载完成必须通过 `ffprobe`；没有 ffprobe 时拒绝声称 media complete。
- status CSV 原子写入；中断后依赖真实文件验证继续，不依赖历史标签。
- ASR dry-run 不创建或修改 state；缺视频不再让整批假成功。
- SRT 连续编号、时间顺序、重叠与视频末端 drift 均进入 validator。

验收：PASS。
## Phase 4 — 可恢复状态机与审批

已完成：

- 每个项目使用 `.autoyy/state.json`，stage 为 `source/subtitle/voiceover/publication/cover/package`。
- 支持 `pending/running/ready/blocked/failed/stale`；中断的 `running` 可恢复为 failed。
- 上游 fingerprint 变化会自动把下游 ready 标记 stale，并撤销相关 approval。
- 新增显式 `state approve` 与 `state force`；强制重跑通过状态机完成，不依赖 agent 删除文件。
- state 写入为原子替换；损坏 schema / JSON 明确报错，绝不静默重建。

验收：PASS。

## Phase 5 — 同行爆款证据库

已完成：

- 动态 evidence library 默认移动到 `<project>/.autoyy/peer-hit-library.csv`，仓库 asset 仅作为 seed/template。
- 统计支持 platform、category、since 过滤，输出 count、median、mean、P75、peak、confidence。
- 未指定平台时按平台分别统计，避免把抖音/B站/快手混成一个总体“最佳模式”。
- 低样本只报告低 confidence，不允许把少量样本包装成确定结论。
- add/import 支持验证、dry-run 与可选 backup。

验收：PASS。
## Phase 6 — 封面与生成物安全

已完成：

- 即梦 prompt 对 6 字主标题 + 8 字副标题实行 hard validation；坏行不写文件。
- 已有标准封面或 prompt 默认不覆盖，只有显式 `--force` 才允许替换。
- unrelated image 不再被误判为“已有双封面”。
- 本地 cover fallback 要求显式 prompt、ffmpeg/ffprobe 和调用方字体，不再内置项目私有映射。
- 双封面先全部生成并校验尺寸，再替换正式文件，避免半成功交付。

验收：PASS。

## Phase 7 — 开源工程化

已完成：

- 新增 `README.md`、`CONTRIBUTING.md`、`SECURITY.md`、`CHANGELOG.md`、`LICENSE`、`.github/workflows/ci.yml`、`.gitattributes`。
- 当前项目许可证为 MIT；vendor 的 Humanizer 保留其独立许可证声明。
- CI 覆盖 Python 3.11 / 3.12、compile、Ruff、85% coverage gate、PS5.1 syntax 与 wrapper integration。
- README / SKILL / workflow-spec / tool-map 已与真实命令和状态语义同步。

验收：PASS。
## Phase 8 — 批量口播质量 Gate

已完成：

- 多 topic 默认一个 writer/context 只处理一个 topic，禁止把多个 SRT 拼成一次写作请求。
- writer 只能先写 `爆款口播稿.candidate.txt`；最终 `爆款口播稿.txt` 必须由 `voiceover promote` 生成。
- batch/package validator 只认最终标准文件，candidate 永远不能代表交付完成。
- `.autoyy/voiceover-quality.json` 绑定 source/script SHA-256，要求 `srt_full_read=true`。
- Humanizer 必须 `mode=embedded`；fact check 与 reviewer 必须绑定当前 source/script hash；reviewer 必须 `independent=true`。
- 最终 Gate 不再信任 Agent 自填的 `pass=true` / `independent=true`：质量记录必须由独立 verifier 使用 `AUTOYY_QUALITY_ATTESTATION_KEY` 生成 HMAC-SHA256 attestation，任何签名后的字段篡改都会使 Gate 失败。
- writer、reviewer、verifier 三方 `run_id` 必须隔离；writer/reviewer 同 run，或 verifier 复用 writer/reviewer run 都会被拒绝。
- evidence 必须包含可定位的 source text 与 script excerpt；允许带 `source_ref + verified_at` 的外部核验来源支持字幕之外的可靠事实。
- 默认强制 4,500–5,500 非空白字符、最少五个可回源直接引语、数字来源检查、evidence 分布覆盖、AI/template shell、重复长段落检测。
- 跨 topic 检测 shared contiguous block、matching-block ratio、开头/结尾相似度和长段落相似度。
- 最多三轮 writer → Humanizer → machine gate → reviewer repair；第三次失败进入 `blocked_quality`，禁止无限重写或放宽阈值。
- `default` profile 保持第三人称；`laorou` 必须显式选择，只覆盖叙述人称，不覆盖事实、Humanizer、evidence、anti-copy 与 promotion Gate。

验收：PASS。

## Release gate 实测证据

```text
python -m autoyy --version       -> autoyy 0.1.0
python -m compileall -q ...     -> PASS
python -m ruff check .          -> PASS
pytest collection               -> 86 tests
pytest + coverage (GitHub 3.12) -> 86.13% total / 85% hard gate PASS
pytest + coverage (GitHub 3.11) -> >85% hard gate PASS
PowerShell 5.1 syntax           -> PASS
PowerShell 7 wrapper test       -> PASS
fresh wheel build/install       -> PASS
autoyy transcribe --help        -> PASS
git diff --check                -> PASS
GitHub Actions run 36798612582  -> 3/3 jobs SUCCESS
```
## 环境边界说明

GitHub CI 以 `windows-latest` + Python 3.11 / 3.12 为发布基线；本地开发机能力不再作为 release PASS 的依据。外部命令是否可用仍由 `autoyy doctor` 在实际运行环境中报告。

`AUTOYY_WORK_ROOT` 未设置时的源码默认值仍为 `D:\自动剪辑`。CLI 与兼容脚本已统一使用 UTF-8 输出策略，避免旧 Windows locale 因中文输出触发编码异常。

## 保留的人工边界

- CI 不访问 live YouTube；网络、账号、版权和平台风控仍需在真实授权环境中验证。
- 外部生成的封面仍需人工目检中文文字是否准确、场景是否匹配；机器 Gate 负责文件、标准命名、比例与流程状态。
- 当前 MIT License 已落盘。如维护者在首次公开发布前希望更换许可证，应同时更新 `LICENSE`、README、CONTRIBUTING 与发布说明。
- AutoYY 仍是 Windows-first 项目；没有宣称 Linux/macOS 全流程等价支持。
## 交接建议

P0 hardening 已在 `fix/p0-hardening` 分支提交并推送；远端 CI 已实际通过。合入默认分支后仍必须以 `main` 的 GitHub Actions 最终结果作为发布依据，不允许仅凭本地结果或本报告宣称 release-ready。

推荐交接验收命令：

```powershell
python -m compileall -q scripts src
python -m ruff check .
python -m pytest --cov=autoyy --cov-report=term-missing --cov-fail-under=85 -q
python -m autoyy doctor
python -m autoyy --version
git diff --check
git status --short
```

结论：P0 hardening 在功能分支已通过机器验收；只有默认分支 `main` 对同一代码再次 GitHub CI 全绿后，才将 P0 标记为最终 CLOSED，并进入下一轮性能与体验优化。
