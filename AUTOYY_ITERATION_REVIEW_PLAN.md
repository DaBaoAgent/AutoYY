# AutoYY 全量代码审查与迭代优化计划

> 审查日期：2026-09-30
> 审查设备：XHP-Redbook
> 项目：`<AutoYY-repository>`
> 基线分支：`main`
> 基线提交：`3069956`
> 远端：`DaBaoAgent/AutoYY`
> 目标：把 AutoYY 迭代为代码干净、高效、流程稳定、可恢复、易测试、易贡献、适合长期维护的优质开源 Codex Skill。

## 1. 本次审查范围

本次不是只看 README 或核心脚本，而是覆盖当前仓库全部有效工程组成：

- 根级 `README.md`、`SKILL.md`、`agents/openai.yaml`、`.gitignore`。
- `references/` 下工作流、工具映射、下载规范、环境事实、内容风格规范。
- `scripts/` 下 9 个 Python 脚本与 1 个 PowerShell 下载脚本。
- `assets/` 下 manifest、即梦封面、同行爆款库、术语表等模板/状态型数据。
- `laorou/` 子技能及风格指南。
- `vendor/blader-humanizer/` 的集成方式、许可证与版本元数据。
- Git 状态、仓库结构、运行环境、依赖可发现性、测试/CI/开源工程文件。
- 真实黑盒测试：错误输入、空目录、非法封面标题、下载失败状态、零字节 ready 文件等。

## 2. 当前基线事实

- 当前仓库共 43 个 tracked files。
- `scripts/`：9 个 Python 脚本、1 个 PowerShell 脚本。
- 当前没有 `tests/`，测试文件数为 0。
- 当前没有 `.github/workflows/` CI。
- 当前没有根级 `LICENSE`、`CONTRIBUTING.md`、`SECURITY.md`、`CHANGELOG.md`。
- Python 脚本 `compileall` 通过，说明至少没有基础语法错误。
- 当前机器：Python 3.12.14、PowerShell 5.1、Node 25.2.1、yt-dlp 2026.08.19、ffmpeg 9.0.2、aria2 1.37.0。
## 3. 结论摘要

AutoYY 已经具备清晰的业务目标和比较完整的内容生产链路，但工程质量目前更接近“个人高效工具集”，还没有达到“可靠开源项目”的标准。主要问题集中在四类：

1. **正确性与退出码不可信**：部分脚本失败后仍返回 0，空目录也可能被判定为成功，Codex 无法据此做可靠阶段验收。
2. **重复逻辑与脚本孤岛**：视频发现、manifest 读取、目录枚举、校验规则、输出结果结构在多个脚本重复实现，规则已经发生漂移。
3. **机器绑定与一次性代码残留**：存在硬编码 `<legacy-local-project>`、本机用户名示例、`D:\自动剪辑`、本地 private-sample-storage 语料路径、项目专用封面映射等。
4. **缺少开源工程护栏**：没有测试、CI、根许可证、贡献规范、依赖声明、版本策略、发布流程、统一日志和兼容矩阵。

目标架构不应把 AutoYY 重写成复杂框架，而应保留现有“Skill + CLI 工具”的简单形态，同时增加一个很薄的公共 Python 包、稳定的命令契约、自动测试和阶段状态机。

## 4. P0：必须先修的阻断问题

### YY-P0-001 下载失败仍返回成功退出码

文件：`scripts/download_from_manifest.ps1`

已实测：manifest 使用不支持 URL 时，状态 CSV 正确记录 `unsupported URL`，控制台也打印 `Failed/incomplete: 1`，但进程退出码仍为 `0`。

风险：Codex、CI 或批处理会把失败阶段当成功，继续写稿、做封面或执行后续动作。

修复要求：
- 任一 active row 最终 `status` 非成功态时，脚本必须 `exit 1`。
- 参数/manifest/schema/依赖错误使用 `exit 2`。
- 全部成功或合法 skip 才返回 `0`。
- 在 README 和 `--help` 中固定退出码契约。
### YY-P0-002 下载状态把零字节文件当作 ready

文件：`scripts/download_from_manifest.ps1`

已实测：创建 0 字节 `高清源视频.mp4` 和 0 字节 `字幕.srt`，manifest 标记 `status=ready` 后，脚本返回 `Completed 1/1`、`ready(skip)`、退出码 0。

修复要求：
- 视频和字幕的存在检查必须同时要求 `Length > 0`。
- 视频至少执行轻量 `ffprobe` 可读性检查；字幕至少执行非空和基本 SRT parse。
- `ready` 只能代表“允许复用已验证产物”，不能绕过实际文件验证。
- 状态 CSV 中增加 `video_bytes`、`subtitle_bytes`、`verified_at` 或等效诊断信息。

### YY-P0-003 空目录被最终校验器判定为成功

文件：`scripts/validate_deliverables.py`

已实测：对完全空目录执行校验，输出 `topic_count: 0`、`incomplete_count: 0`，退出码为 0。

修复要求：
- 默认情况下 0 个目标目录必须返回运行错误 `exit 2`。
- 增加显式 `--allow-empty`，仅测试/特殊场景使用。
- 支持 `--expected-count N`；发现数量不符直接失败。
- 最终总体验收不得仅根据 `incomplete_count == 0` 判断成功。

### YY-P0-004 字幕校验器同样存在空集合假成功

文件：`scripts/validate_subtitles.py`

已实测：空目录在 ffprobe 可用时返回 `folder_count: 0` 且退出码 0。

修复要求与 YY-P0-003 相同，并统一使用公共 target discovery 逻辑。
### YY-P0-005 并行下载分支存在 runspace 作用域缺陷

文件：`scripts/download_from_manifest.ps1`

代码审查发现：`$processRowScript` 内部会调用 `Get-SubtitleLanguage` 和 `Add-JsRuntimeArgument`，但进入 `ForEach-Object -Parallel` 的新 runspace 后，仅重新声明了 `Add-OptionalArgument` 和 `Invoke-YtDlp`。当前机器没有 PowerShell 7，无法本机黑盒复现并行分支，但从作用域结构看，该分支不具备自包含性。

修复要求：
- 不要依赖父 runspace 的函数可见性。
- 优先将单行处理逻辑抽成独立 `.psm1`/Python CLI；并行 worker 只调用稳定入口。
- 如果暂时保留 scriptblock，所有依赖函数必须在 worker 内显式定义或通过模块导入。
- CI 必须在 Windows + PowerShell 7 上覆盖 `-Parallel 2`。

### YY-P0-006 manifest folder_name 存在路径越界风险

受影响：下载、转录、即梦提示词以及未来所有按 manifest/CSV 创建目录的脚本。

当前代码直接执行 `Join-Path $OutputRoot $folderName` 或 `os.path.join(root, name)`；类似 `..\outside` 的输入可能逃离工作根目录。

修复要求：
- 新增统一 `safe_child_path(root, relative_name)`。
- 拒绝绝对路径、`..`、盘符、UNC、空名称和解析后不在 root 下的路径。
- Windows 下同时处理大小写、路径分隔符和保留设备名。
- 对 CSV 中重复 `folder_name` 默认报错，不允许静默覆盖。

## 5. P1：高优先级正确性与可维护性问题

### YY-P1-001 SRT“编号连续”规则实际没有实现

文件：`scripts/validate_subtitles.py`

已实测：编号 `1` 后直接跳到 `3`，`parse_srt()` 返回空 issues。文档声称会校验“编号连续”，实现只检查“编号是数字”。

要求：解析并比较实际编号；明确是否要求从 1 开始、严格递增、连续且不重复，并写单元测试。
### YY-P1-002 ffprobe 失败可能被当成字幕校验通过

`validate_subtitles.py` 中 `video_duration()` 返回 `None` 时，当前逻辑不会自动追加 issue；只要 SRT 语法本身没报错，某些“未完成视频时长核验”的目录仍可能显示 valid。

要求：
- ffprobe 非零、超时、输出无法解析都必须成为明确 issue。
- 捕获 `subprocess.TimeoutExpired`，不能让单个坏视频击穿整个 batch。
- JSON 结果增加 `duration_probe_status` 和诊断原因。

### YY-P1-003 manifest 指向不存在目录时异常处理不完整

`transcribe.py` 和 `validate_subtitles.py` 在 manifest 模式下直接构造 `root / folder_name`；`find_video()` 内 `iterdir()` 可能在进入当前 try/catch 前抛 `FileNotFoundError`，导致整个批次中止。

要求：单目录失败必须结构化记录，批次继续；只有全局前置条件失败才中止整个命令。

### YY-P1-004 即梦标题长度错误只警告仍写文件并返回 0

文件：`scripts/gen_jimeng_cover_prompts.py`

已实测：主标题 1 字、副标题 2 字时，脚本打印警告但仍创建 `封面提示词-即梦.txt`，退出码 0；这与 SKILL 中“script validates 6-char/8-char title lengths”不一致。

要求：
- 默认严格模式：长度错误不写文件，该 row 失败，最终 exit 1。
- 如确有兼容需求，可提供显式 `--lenient`。
- CSV 缺字段、目录不存在、重复目录名也要纳入失败统计，而不是一律 0。

### YY-P1-005 即梦提示词可能覆盖已有用户文件

当前只要目录内图片少于 2 张，就直接以 `w` 覆盖 `封面提示词-即梦.txt`，与 SKILL 的“existing user files are authoritative / no silent overwrite”原则冲突。

要求：默认不覆盖；增加 `--force`；写临时文件后原子替换；输出 changed/skipped/failed 统计。
### YY-P1-006 最终交付校验与发布信息校验规则漂移

`validate_deliverables.py` 会先过滤空行再判断两行，因此某些带额外空白行的 `发布信息.txt` 可能在总校验中通过；`validate_publication_info.py` 则按物理行严格失败。

要求：发布信息只保留一个权威 validator；总校验器直接调用公共函数，不复制规则。

### YY-P1-007 视频/字幕发现逻辑重复且行为不一致

`transcribe.py`、`validate_subtitles.py`、`validate_deliverables.py`、PowerShell 下载器各自维护视频扩展名、主视频优先级、SRT 选择与完成条件。

后果：新增格式或调整标准时容易只改一处，出现“下载器认为完成、验证器认为缺失”的状态分裂。

要求：抽到公共模块，至少统一：
- `VIDEO_EXTENSIONS`
- `IMAGE_EXTENSIONS`
- `find_primary_video()`
- `find_primary_subtitle()`
- `is_nonempty_file()`
- topic directory discovery
- manifest target discovery

### YY-P1-008 两个下划线脚本是一次性项目残留

文件：`scripts/_srt_to_text.py`、`scripts/_validate_batch.py`

问题：硬编码 `<legacy-local-project>`，没有 argparse、没有退出码契约、没有复用公共规则，其中 `_validate_batch.py` 与正式 validators 功能重复。

处理方案：
- `_srt_to_text.py` 改为通用 `srt_to_text` CLI 或并入 subtitle 模块。
- `_validate_batch.py` 删除；需要的能力合并到正式 validator。
- 删除前先在 Git 历史保留即可，不需要继续把实验脚本放主分支。

### YY-P1-009 `build_topic_covers_from_video.py` 存在项目专用硬编码

`MISSING_TEXT` 内写死一批监狱选题目录与文案，这不应存在于通用开源核心脚本中。

要求：封面文字必须来自 manifest/CSV/发布信息/显式参数；项目专用映射移入示例 fixture 或直接删除。
### YY-P1-010 本机环境事实混入公开仓库

`references/env-facts.md`、错误提示和 laorou 风格指南含具体本机路径、用户名示例、junction 约定、private-sample-storage 私有语料路径等。

要求：
- 公开文档只保留可移植配置和示例占位符。
- 本机事实迁到不提交的 `references/local.env-facts.md` 或用户级配置。
- `.gitignore` 显式加入本地事实文件、`desktop.ini`、临时状态、生成报告。
- 不依赖用户全局 `.gitignore_global` 才保持仓库干净。

### YY-P1-011 根项目缺少依赖与环境契约

当前 README 只展示 clone，没有明确 Python/Pillow/yt-dlp/ffmpeg/Node/aria2/faster-whisper/FunASR 的安装层级和哪些是可选依赖。

要求：引入 `pyproject.toml`，最少定义：
- Python 支持版本。
- `Pillow` 等基础运行依赖。
- `asr-whisper` / `asr-funasr` optional extras，避免所有用户安装重型模型依赖。
- dev extras：pytest、ruff；如采用类型检查，再加入 mypy/pyright。
- Ruff/pytest 配置统一放在 pyproject。

### YY-P1-012 同行爆款库统计方法过于粗糙

`peer_hit_library.py --patterns` 直接跨平台、跨分类、跨时间按平均播放量排名；极端爆款会强烈拉高均值，抖音/B站/快手播放量也不可直接横比。

要求：
- 默认按 platform 分层，必要时再按 category 分层。
- 同时输出 sample count、median、mean、P75/peak；样本太少必须标记低置信度。
- 支持 `--platform`、`--category`、`--since`。
- 排序默认使用稳健统计量，不允许仅凭一个极端样本成为“最佳模式”。
- 保留原始数据，不在导入时做不可逆归一化。

### YY-P1-013 运行时直接修改 tracked 数据文件

`peer_hit_library.py` 默认修改 `assets/peer-hit-library.csv`，会让正常使用技能后仓库自动变脏。

建议改为“只读 seed + 用户状态库”模式：仓库内 `assets/peer-hit-seed.csv` 只读；真实持续学习数据默认放工作项目 `.autoyy/peer-hit-library.csv` 或用户数据目录，并允许 `--library PATH` 显式指定。
## 6. P2：中优先级工程质量问题

- `build_topic_covers_from_video.py` 有未使用的 `math`、`os` import，ImageDraw 采用动态 import，可直接清理。
- cover 视频时长通过解析 ffmpeg stderr 获取，建议统一改 `ffprobe` JSON，避免输出格式变化导致解析失败。
- 多个 subprocess 没有 timeout；坏文件或外部工具卡住时会无限等待。
- JSON report 写入前不创建父目录，用户传不存在的 `--json-out` 父目录时会直接失败。
- 多个批处理脚本直接写目标文件，缺少临时文件 + `os.replace()` 的原子写入策略。
- CLI 输出中英文混杂；建议机器字段固定英文枚举，人类提示统一中文。
- `transcribe.py` 文档宣称有 `--only-missing`，实际 argparse 没有该参数，应删除文档或实现兼容别名。
- `transcribe.py` 在 import 阶段设置 `HF_ENDPOINT=https://hf-mirror.com`，对通用开源项目副作用过强；应改为参数/环境默认策略。
- `validate_subtitles.py` 对任何字幕重叠都判失败；应提供小幅 overlap tolerance，并区分 warning/error。
- `validate_deliverables.py` 的 banned-pattern 规则只是 `content-style.md` 的子集，必须明确“自动硬规则”和“人工语义规则”的边界，避免假装已自动覆盖全部文案质量。
- `gen_jimeng_cover_prompts.py` 在循环内 import glob 和声明扩展名常量，虽不影响速度，但应整理为模块级常量。
- `peer_hit_library.py` 导入更新计数即使没有实际字段变化也会 `updated += 1`，统计含义不准确。
- `agents/openai.yaml` 与根 SKILL 硬编码工作根目录，建议改用 `${AUTOYY_WORK_ROOT}` 语义并由 Skill 在缺省时回退到 Windows 默认值。
- README 没有故障排查、兼容矩阵、验证命令和最小可运行示例，首次贡献者无法快速确认安装是否正确。

## 7. 范围与架构问题

### 7.1 `laorou/` 当前属于“仓库内第二套 Skill”，但边界不清

根 AutoYY 规定默认第三人称叙述；laorou Skill 明确要求第一人称“咱/我”、固定艾伦签名，两者是不同 voice profile。当前根 SKILL 没有明确路由到 laorou，也不说明安装时 nested skill 是否会被发现。

迭代时二选一，优先方案 A：
- A：把 laorou 定义为 AutoYY 的可选 voice profile，根工作流通过 `--voice-profile default|laorou` 或明确 Skill 路由调用。
- B：把 laorou 拆成独立仓库/独立可安装 Skill，并从 AutoYY 删除。

不要维持现在这种“代码在一起但主流程不知道它是否存在”的状态。
### 7.2 `build_topic_covers_from_video.py` 当前是孤立能力

根 SKILL 的封面阶段主要描述 image generation 和即梦提示词，`build_topic_covers_from_video.py` 没有进入 Resources/tool-map，也没有测试。

处理要求：明确它是正式 fallback 还是实验工具：
- 若正式保留：加入工具映射、依赖、CLI 文档、测试和验收。
- 若只是历史实验：移到 `examples/` 或删除，避免维护双封面管线。

## 8. 推荐目标目录结构

建议保留现有用户入口，同时把共享逻辑收敛到一个薄包：

```text
AutoYY/
├── pyproject.toml
├── README.md
├── LICENSE                 # 由维护者明确选择
├── CONTRIBUTING.md
├── SECURITY.md
├── CHANGELOG.md
├── SKILL.md
├── agents/openai.yaml
├── src/autoyy/
│   ├── __init__.py
│   ├── cli.py              # 可选统一入口，保持薄
│   ├── config.py           # 工作根、环境变量、默认值
│   ├── paths.py            # safe_child_path、topic discovery
│   ├── manifest.py         # schema、读取、重复/字段校验
│   ├── media.py            # 视频/字幕发现、ffprobe
│   ├── subtitles.py        # parse/validate/srt-to-text
│   ├── publication.py      # 发布信息唯一权威 validator
│   ├── deliverables.py     # 组合 validator，不复制规则
│   ├── state.py            # 阶段状态、输入 hash、恢复点
│   └── result.py           # exit code / JSON result contract
├── scripts/                # 保留兼容 wrapper
├── assets/                 # 只读模板/seed/参考图
├── references/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── .github/workflows/ci.yml
```

原则：不要一次性把所有逻辑重写成大框架；先把重复且容易漂移的规则抽出来，原脚本改成薄 wrapper，保证已有 Codex 调用路径继续可用。
## 9. 分阶段开发与验收总路线

必须按 Phase 0 → Phase 8 顺序执行。Codex 每完成一个 Phase 后停止开发，先提交该阶段验收结果；只有本阶段全部 gate 通过才进入下一阶段。

### Phase 0：建立基线与防回归护栏

目标：先让现有行为可测，不在没有测试的情况下大改。

任务：
1. 新建 `pyproject.toml`，定义 Python 版本、基础依赖、dev extras、pytest/ruff 配置。
2. 建立 `tests/unit`、`tests/integration`、`tests/fixtures`。
3. 将本次确认的 bug 先写成失败测试：empty-root、非连续 SRT 编号、非法即梦标题、下载失败 exit code、zero-byte ready。
4. 为 `validate_publication_info.py` 当前正确行为建立 golden tests。
5. 为 manifest template 做 schema fixture。
6. 新增最小 `.github/workflows/ci.yml`：Windows runner + Python 3.11/3.12；PowerShell 5.1 语法检查；PowerShell 7 下载器集成测试。
7. 增强 `.gitignore`：`desktop.ini`、`.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`、coverage、local env facts、临时状态。
8. 不在此阶段改变业务规则，除非为了让测试可注入外部工具路径。

Phase 0 验收：
```powershell
python -m compileall -q scripts src
python -m pytest -q
python -m ruff check .
git status --short
```
要求：测试框架正常工作；已知 bug 测试可用 xfail 精确标记，但不得删除/弱化断言；仓库没有测试产生的脏文件。

交付提交建议：`test: establish regression baseline and CI`。
### Phase 1：修复所有 P0 正确性问题

目标：任何失败都能被 Codex/CI 正确识别，任何“完成”都必须有真实产物支撑。

任务：
1. 修复下载器退出码：成功 0、批次存在失败 1、参数/依赖/schema 2。
2. ready/complete 检查加入非零大小和轻量可读性验证。
3. 修复 deliverables/subtitles 空目标假成功；加入 `--allow-empty` 和 `--expected-count`。
4. 为所有基于 CSV/manifest 的目录创建统一 path containment 检查。
5. 修复 PowerShell 7 并行 worker 作用域，确保 worker 自包含。
6. 对 duplicate folder_name、缺少必填 header、空 URL、非法 URL 给结构化错误。
7. 批处理失败时保留每行结果，不能因为单行失败丢失其他状态。
8. 将所有 Phase 0 对应 xfail 转为正常 pass。

Phase 1 必测场景：
- unsupported URL → exit 1。
- 0 字节 video/SRT + ready → 不得 skip 为完成。
- empty root → exit 2。
- `folder_name=..\outside` → 拒绝且不在 root 外创建文件。
- duplicate folder_name → 拒绝。
- PowerShell 7 `-Parallel 2` + fake yt-dlp → 两行都能正常完成/失败并写状态。
- Ctrl+C/中断后再次运行不会破坏已验证完成项。

Phase 1 验收条件：P0 测试 100% 通过；没有依赖真实 YouTube 网络的 CI 测试，外部命令全部用 fake executable/fixture 模拟。

提交建议：`fix: make pipeline completion and exit codes trustworthy`。
### Phase 2：抽公共核心，消除规则漂移

目标：重复规则只保留一份权威实现，现有脚本继续兼容。

任务：
1. 建立 `src/autoyy/paths.py`：安全子路径、topic discovery、非空文件判断。
2. 建立 `src/autoyy/manifest.py`：dataclass/TypedDict schema、必填字段、枚举、重复检查、UTF-8-SIG 兼容。
3. 建立 `src/autoyy/media.py`：视频/字幕扩展、主文件发现、ffprobe 元数据。
4. 建立 `src/autoyy/subtitles.py`：SRT parser、连续编号、时间轴、允许重叠阈值、文本提取。
5. 建立 `src/autoyy/publication.py`：两行格式、标题长度、标签唯一性/格式、legacy field 检测。
6. 建立 `src/autoyy/result.py`：统一 success/warning/error 结构及 exit code 映射。
7. 将 `validate_*` 和 `transcribe.py` 改为薄 CLI，调用公共模块，不再复制常量/规则。
8. `_srt_to_text.py` 能力并入公共 subtitle CLI；删除 `_validate_batch.py`。
9. 保持原脚本路径和主要参数名可用，避免 SKILL 和既有自动化失效。

Phase 2 验收：
- 搜索 `VIDEO_EXTENSIONS`、publication 两行规则、manifest folder discovery，不应存在多套互相独立实现。
- 原有 CLI help 仍可运行。
- 所有 Phase 0/1 测试继续通过。
- 新公共模块单测覆盖率目标 ≥ 85%，整个 Python 代码库先达到 ≥ 75%。

提交建议：`refactor: centralize manifest media subtitle and validation rules`。
### Phase 3：补齐字幕、ASR 与外部工具鲁棒性

目标：长视频批处理能稳定失败、稳定恢复、稳定诊断。

任务：
1. `validate_subtitles.py`：补编号连续、ffprobe failure、timeout、overlap tolerance、无目标错误。
2. 增加字幕覆盖率指标：末字幕时长/视频时长、首字幕起点、可选最大长空白段；区分 error 与 warning。
3. `transcribe.py`：manifest 不存在目录时逐项失败而不是全局崩溃。
4. 删除不存在的 `--only-missing` 文档或实现兼容别名；CLI 文档与 argparse 必须自动测试一致。
5. `HF_ENDPOINT` 改为显式配置：尊重已有环境变量；可提供 `--hf-endpoint`，不得在 import 阶段强改全球默认。
6. 后端依赖启动时做 preflight：明确提示安装哪个 optional extra；不要对每个目录重复报相同 ImportError。
7. 所有 subprocess 加合理 timeout、命令路径验证和截断后的诊断输出。
8. 临时 wav 空间不足、ffmpeg 失败、ASR 未识别语音均输出结构化 reason code。
9. 写 SRT 使用原子文件；只有完整写入后才替换 `字幕.srt`。

Phase 3 验收 fixture：正常 SRT、跳号、重复号、逆序、轻微重叠、严重重叠、空字幕、损坏视频、ffprobe 超时、manifest 缺目录、已有字幕 skip。

提交建议：`fix: harden subtitle validation and ASR recovery`。

### Phase 4：建立轻量状态机与真正可恢复工作流

目标：让“resume”从脚本各自猜测文件存在，升级为可审计的阶段状态。

建议每个项目根建立 `.autoyy/state.json`，不放进技能仓库。每个 topic 记录：
- topic id / folder name。
- 各阶段 `pending|running|ready|blocked|failed|stale`。
- 输入文件 hash 或 size+mtime fingerprint。
- 输出路径、工具版本、开始/完成时间。
- 最后错误 reason code。
- 依赖阶段的 fingerprint。

规则：上游输入变化时，下游自动标 stale；只有验证器确认后才从 running/pending 进入 ready；禁止仅凭“文件名存在”认定 ready。
Phase 4 任务：
1. 新增 `state.py` 和版本化 state schema，例如 `schema_version: 1`。
2. 下载、字幕、文案、发布信息、封面、最终校验都通过统一状态 API 更新。
3. state 写入必须 atomic；中断时最多留下 running，可在下次启动时恢复为 pending/failed。
4. `--resume` 默认只重跑 failed/stale/missing；`--force-stage` 可明确重跑某阶段。
5. 人工批准的文案/封面增加 `approved: true`，没有显式 force 不得覆盖。
6. 状态文件只保存元数据，不保存 cookie、token、代理凭据等秘密。

Phase 4 验收：
- 中断下载、转录、提示词写入后可安全重跑。
- 修改字幕后文案/发布/最终包变 stale，但原文件不被自动删除。
- approved cover/script 默认不可覆盖。
- state 损坏时能给出可恢复错误，不静默重建并丢历史。

提交建议：`feat: add resumable stage state and stale propagation`。

### Phase 5：改进同行爆款学习库与发布信息质量

目标：保留“持续学习”价值，同时避免粗暴平均数误导选题。

任务：
1. 将 tracked seed 和用户动态库分离。
2. CLI 增加 `--library`，默认解析项目 `.autoyy/peer-hit-library.csv`。
3. `--patterns` 支持 platform/category/since 过滤。
4. 输出 count、median、mean、P75、peak；少样本显示 low-confidence。
5. 可增加 `engagement_rate`，但只能在 views/likes/comments 数据同时可靠时计算。
6. 导入时验证 header、日期、非负数值、hook_pattern 枚举；未知模式允许 warning + 保留 raw，而非静默污染。
7. 导入/保存原子写入并生成可选 backup。
8. `--dry-run` 显示 add/update/no-change，不改库。
9. 发布标题生成仍由模型完成，统计工具只提供证据，不在脚本里硬编码“最佳标题”。

Phase 5 验收：跨平台样本不再默认直接比较；极端单样本不会在无置信度提示下被称为最优模式；动态使用不导致 git dirty。
### Phase 6：统一封面管线与覆盖策略

目标：明确“即梦提示词”和“本地视频帧封面”两个能力的主次关系，并让覆盖行为可预测。

任务：
1. 移除 `build_topic_covers_from_video.py` 的 `MISSING_TEXT` 项目专用映射。
2. 封面文案统一从显式输入读取，不从历史目录名猜测。
3. 即梦生成器标题 6+8 字改为严格校验，错误 row 不落盘。
4. 目录中“任意 2 张图片即视为已有封面”的策略改为可配置；默认优先识别标准封面文件名和 state approval。
5. 已存在提示词/封面默认 skip，不静默覆盖；提供 `--force`。
6. 本地封面脚本用 ffprobe 获取时长，subprocess 加 timeout。
7. 每次输出后立即验证分辨率、比例、非零大小；本地文字渲染再检查字体 glyph 可用性。
8. Pillow 作为明确基础依赖或将该脚本移到 optional `cover-local` extra。
9. 在 `tool-map.md` 中明确：哪一种是默认路径、哪一种是 fallback。

Phase 6 验收：
- 非法 6+8 标题 exit 1 且不创建文件。
- 已批准封面在无 force 时不会变化。
- 生成一半被中断后重跑不会误认为整组完成。
- 本地 fallback 对短视频/坏视频/缺字体都有明确失败码。

提交建议：`refactor: make cover generation strict resumable and explicit`。

### Phase 7：配置、文档与开源工程化

目标：陌生贡献者 clone 后可以知道依赖、启动、测试、边界和许可证。

任务：
1. 新增 `LICENSE`，由维护者明确选择项目许可证；不要由 Codex 擅自替维护者决定法律许可。
2. 新增 `CONTRIBUTING.md`：环境、分支、测试、提交、PR、fixture 规则。
3. 新增 `SECURITY.md`：漏洞报告方式、cookie/token/路径越界等安全边界。
4. 新增 `CHANGELOG.md`，采用 Keep a Changelog 或等效简洁格式。
5. README 增加 Quickstart、Requirements、Doctor、Examples、Troubleshooting、Compatibility、Testing、License。
6. `references/env-facts.md` 改成通用环境说明，本机事实移出版本控制。
7. 根工作目录支持 `AUTOYY_WORK_ROOT`，Windows 默认值仅作为 fallback。
8. README 明确 Windows-first 或实际支持的 OS 矩阵，不做未经 CI 验证的跨平台承诺。
Phase 7 文档验收：
- 全新目录 clone 后，按 README 能完成安装、`--help`、doctor、测试和最小 fixture 验证。
- 文档中不存在 `<user>`、`<legacy-local-project>`、private-sample-storage 私有路径等机器绑定信息。
- `SKILL.md`、tool-map、README、CLI help 的参数/流程描述保持一致。
- vendor 继续保留 MIT LICENSE 与来源/version 元数据，根项目许可证与 vendor 许可证边界清楚。

提交建议：`docs: make AutoYY contributor-ready and portable`。

### Phase 8：统一入口、doctor、自检与发布候选验收

目标：给 Codex 和人类一个稳定的项目入口，但不牺牲原脚本兼容性。

推荐增加：
```powershell
python -m autoyy doctor
python -m autoyy validate <project-root>
python -m autoyy transcribe <project-root>
python -m autoyy publication validate <project-root>
python -m autoyy peer patterns --platform douyin
```

`doctor` 至少检查：Python 版本、PowerShell 版本、yt-dlp、ffmpeg/ffprobe、Node/Deno、aria2 optional、ASR backend optional、工作根可写性、关键 assets 是否存在。

要求：
- doctor 只检查，不擅自安装软件、不改系统配置。
- 原 `scripts/*.py` / `.ps1` 入口继续作为兼容 wrapper 至少保留一个 release cycle。
- CLI 机器可读输出统一提供 `--json` 或 `--json-out`。
- 所有命令遵循 0/1/2 退出码契约。
- 增加项目版本，例如 `0.1.0` 或由维护者确定的初始语义化版本。

Phase 8 验收后才允许创建 release tag。

提交建议：`feat: add stable AutoYY CLI doctor and release gates`。
## 10. Codex 开发执行协议

Codex 必须遵守以下规则，不能以“整体重构”为理由跳过阶段验收。

### 10.1 每个 Phase 开始前

1. 读取本文件对应 Phase 的全部要求。
2. 执行 `git status --short`，记录起始工作区状态。
3. 执行当前测试基线，确认失败项与本 Phase 目标一致。
4. 列出本阶段准备修改的文件；超出阶段范围时说明原因。
5. 不主动升级无关依赖，不顺手重排无关文档，不混入大规模格式化噪音。

### 10.2 开发过程中

- 一次修一个行为簇，先测试后实现或同步补测试。
- 对 bug 必须有能够在旧代码失败、修复后通过的回归测试。
- 不删除失败测试来获得绿色 CI。
- 不降低阈值、放宽断言或增加 blanket ignore 来掩盖问题。
- 所有文件写入/覆盖行为都要考虑中断与重复执行。
- 所有外部命令调用都要考虑：不存在、非零退出、timeout、乱码输出、部分产物。
- 不把真实 cookie、token、代理、用户路径、版权素材写进 fixture。
- 测试网络流程使用 fake executable 或离线 fixture，CI 不依赖 YouTube 可用性。

### 10.3 每个 Phase 完成时必须回报

```text
Phase: X
Changed files: ...
Fixed issue IDs: ...
Tests added: ...
Commands run: ...
Test result: pass/fail
Known remaining issues: ...
Git diff summary: ...
Ready for phase acceptance: yes/no
```

若 `Ready ... = no`，不得继续下一阶段。
## 11. 分批次验收策略

每个 Phase 建议再拆成 1-3 个小批次，每批次独立可回滚、可测试、可 review。

### 批次 A：行为修复
- 只改与 issue 直接相关的最少实现。
- 新增对应回归测试。
- 不做目录重构。

### 批次 B：结构收敛
- 在行为已有测试保护后抽公共函数/模块。
- 对比重构前后 JSON/exit code/文件输出，保证兼容。
- 任何 CLI breaking change 必须写 migration note。

### 批次 C：文档与清理
- 更新 SKILL、README、tool-map、help。
- 删除已被替代的一次性脚本或 deprecated code。
- 跑完整测试与静态检查。

每一批次建议一个独立 commit。不要把“修 bug + 大重命名 + 文档改写 + 新功能”塞进同一个提交。

## 12. 强制质量门禁

进入下一 Phase 前必须同时满足：

1. `python -m compileall -q scripts src` 通过。
2. `python -m pytest -q` 全绿；允许的 skip 仅限明确缺失 optional runtime，不允许 xfail 长期遗留 P0/P1。
3. `python -m ruff check .` 通过。
4. 若启用 formatter，`ruff format --check .` 或等效检查通过。
5. Windows PowerShell 5.1 路径至少完成语法/顺序下载器测试。
6. PowerShell 7 CI 完成 parallel integration test。
7. 生成型脚本测试结束后工作区无意外新文件。
8. `git diff --check` 通过。
9. 文档参数与实际 `--help` 不冲突。
10. 所有新增 destructive/overwrite 行为都必须有显式 `--force` 或等效用户意图。

性能原则：优先减少重复 I/O、重复 ffprobe、重复模型加载和重复目录扫描；不要为几十个 topic 引入数据库或常驻服务。
## 13. 最终总体验收矩阵

### 13.1 安装与自检
- 新 clone 能按 README 建立环境。
- `python -m autoyy doctor` 能明确区分 required / optional dependency。
- 缺 yt-dlp、ffmpeg、ASR backend 时提示可操作，不抛长 traceback。

### 13.2 Manifest 与路径安全
- 正常 template 可读取。
- 缺必填 header、重复 folder、非法 enum、绝对路径、`..` 越界都失败。
- UTF-8 与 UTF-8-SIG 均可读。

### 13.3 下载与恢复
- plan-only 不写媒体。
- 顺序模式与并行模式结果语义一致。
- partial 文件不被误判 ready。
- zero-byte 文件不被误判完成。
- failed row → exit 1，global input error → exit 2。
- rerun 只跳过经过验证的 ready 项。

### 13.4 字幕与 ASR
- 标准 SRT、编号、时轴、重叠、漂移、ffprobe failure 均有 fixture。
- 无字幕时 ASR 能在 optional backend 可用时生成原子输出。
- manifest 中坏目录不影响其他目录完成。

### 13.5 文案与 Humanizer gate
- 根 Skill 仍强制 source/SRT-first 和 Embedded Humanizer。
- 自动 validator 只声称检查可确定规则，不虚假声称完成语义事实核验。
- approved script 不会被 resume 自动覆盖。

### 13.6 发布信息
- 精确两物理行、标题 ≤25、5 个合法且不重复 hashtags。
- bulk recursive audit 与 final validator 使用同一权威函数。
- peer statistics 有平台/分类过滤与样本置信度。

### 13.7 封面
- 6+8 标题严格失败而非警告后继续。
- 3:4 / 4:3 比例可机器验证。
- approved cover 默认不可覆盖。
- 即梦 prompt 与 local fallback 的职责清楚且被文档覆盖。
### 13.8 状态机与恢复
- 每个 stage 有明确状态和 reason code。
- 上游输入变化会让下游 stale，而不是偷偷沿用旧结果。
- state 原子写入；中断后可恢复。
- state 不保存秘密。

### 13.9 仓库卫生
- 正常运行 AutoYY 不修改 tracked asset。
- `git status --short` 在测试/doctor 后保持干净。
- 无 `desktop.ini`、`__pycache__`、临时 JSON、真实工作项目进入 Git。
- 无硬编码真实用户名、私有盘路径和一次性项目目录。

### 13.10 开源工程
- 根 LICENSE 已由维护者确认。
- CONTRIBUTING/SECURITY/CHANGELOG 存在且准确。
- CI 覆盖受支持 Python/PowerShell 组合。
- README 的安装命令在 clean environment 验证过。
- vendor 第三方许可证和版本来源保留。

## 14. 兼容性约束

以下行为除非单独批准，不应在本轮迭代中破坏：

- 根 Skill 名仍为 `autoyy`。
- 现有 `scripts/download_from_manifest.ps1` 路径继续可调用。
- 现有 `scripts/transcribe.py`、`validate_*.py` 的核心参数尽量保留。
- manifest 当前 16 个字段保持兼容；新增字段必须 optional 或通过 schema version 演进。
- 标准交付文件名继续使用 `高清源视频.*`、`字幕.srt`、`爆款口播稿.txt`、`发布信息.txt`、`封面-3比4.*`、`封面-4比3.*`。
- 不改变 Humanizer 的 vendor 文件许可证内容。
- 不自动下载未授权媒体、不绕过 DRM/付费墙/区域控制、不默认使用浏览器 cookie。

## 15. 不建议做的过度设计

- 不引入数据库保存几十个 topic 的状态；JSON 足够。
- 不引入 Web 后端、队列服务或微服务。
- 不为了统一 CLI 删除所有兼容脚本。
- 不把模型/视频大文件纳入 Git LFS，工作产出本就不应进入技能仓库。
- 不用复杂插件系统替代简单 voice profile/config。
- 不为了“100% coverage”写无价值断言；优先覆盖状态转换、路径安全、退出码和文件写入边界。
## 16. 本次审查已确认的问题证据摘要

| ID | 级别 | 证据状态 | 当前表现 |
|---|---|---|---|
| YY-P0-001 | P0 | 已黑盒复现 | 下载器 row 失败但进程 exit 0 |
| YY-P0-002 | P0 | 已黑盒复现 | 0 字节 video/SRT 被 ready(skip) |
| YY-P0-003 | P0 | 已黑盒复现 | deliverables 空目录 exit 0 |
| YY-P0-004 | P0 | 已黑盒复现 | subtitles 空目录 exit 0 |
| YY-P0-005 | P0 | 代码审查确认风险 | Parallel worker 依赖父作用域函数；当前机无 PS7，待 CI 复现 |
| YY-P0-006 | P0 | 代码审查确认 | folder_name 未做 root containment |
| YY-P1-001 | P1 | 已黑盒复现 | SRT 1→3 编号跳号无 issue |
| YY-P1-004 | P1 | 已黑盒复现 | 1+2 字即梦标题仍写文件且 exit 0 |
| YY-P1-006 | P1 | 代码对比确认 | 两个 publication validator 空行规则不同 |
| YY-P1-008 | P1 | 文件审查确认 | 两个脚本硬编码 `<legacy-local-project>` |
| YY-P1-009 | P1 | 文件审查确认 | 本地封面脚本硬编码具体监狱选题映射 |
| YY-P1-013 | P1 | 代码审查确认 | peer library 默认写 tracked asset |

## 17. Definition of Done

只有同时满足以下条件，才可以宣布本轮 AutoYY 总体迭代完成：

1. P0 全部关闭并有回归测试。
2. P1 全部关闭，或维护者明确记录延期原因和后续 issue。
3. 所有正式 CLI 失败语义、退出码、JSON 结果一致。
4. 重复的 manifest/media/subtitle/publication 核心规则已经收敛到公共模块。
5. 测试、ruff、compile、Windows CI 全绿。
6. 顺序与 PowerShell 7 并行下载器通过离线集成测试。
7. 空目录、零字节、路径越界、缺依赖、坏字幕、坏视频都不会假成功。
8. resume 能依据验证状态恢复，approved 资产不会被静默覆盖。
9. 正常运行不污染 Git 工作区。
10. 新用户可以仅靠 README 完成安装、自检和最小工作流。
11. 开源许可证由维护者确认并落盘，第三方 LICENSE 保留完整。
12. `SKILL.md`、README、tool-map、实际 CLI 行为一致。

完成以上条件后，再创建 release tag，并在 CHANGELOG 中记录 breaking changes、migration 和已知限制。
## 18. 多子目录批量任务防偷懒硬门槛（Hermes / Workbuddy / Codex 必须遵守）

本节是强制规则，优先级高于前文任何“尽量”“建议”式描述。目的不是提高平均质量，而是禁止批量任务中出现少写、套模板、AI 腔、编造事实后仍被标记完成。

### 18.1 核心原则：逐目录事务，不允许整批平均验收

1. 每个 `<NN-中文选题>` 都是一个独立事务，独立读取素材、独立起草、独立 Humanizer、独立事实复检、独立验证、独立状态记录。
2. **禁止一次模型调用生成两个或以上目录的最终 `爆款口播稿.txt`。** 批量入口只能负责枚举目录和调度，写稿阶段必须逐目录启动独立上下文。
3. 下载、ffprobe、ASR 等确定性 I/O 可以并行；**文案生成默认串行（writer concurrency = 1）**。以后若开放 >1，只能是完全隔离的 worker，每个 worker 一次只持有一个 topic 的素材。
4. 一个目录失败，不得用其他目录的成功抵消。`9/10` 不是“完成”，而是整批 `failed/incomplete`，进程必须 exit 1。
5. 未通过最终 gate 的稿件只能保留为 draft，不得命名或提升为 `爆款口播稿.txt`。
6. agent 不得因为目录多、上下文长、时间紧而缩短字数、跳过完整 SRT、跳过 Humanizer 或省略事实复检；做不到就标记 blocked。

### 18.2 单目录写稿前置 Gate（缺一项禁止起草）

每个目录在进入 writer 前必须生成/确认内部证据记录，例如 `.autoyy/evidence/<topic-id>.json`，至少包含：
- 主 SRT 路径、文件大小、hash、字幕块数量、首末时间；
- writer 已完整读取 SRT 的确认状态，不允许只读开头/抽样片段；
- 关键人物、机构、地点、日期、数字、事件、直接引语及对应字幕块/时间范围；
- 额外补充来源及来源类型；模型常识不得伪装成来源；
- `source_insufficient=true` 时直接 block，不允许靠常识扩写凑字数。

硬规则：
1. SRT 缺失、为空、解析失败或未通过 subtitle gate → 禁止写稿。
2. 默认 AutoYY 文案的重要事实必须能映射到 SRT 或显式 verified source。
3. 每个数字、日期、人名、机构名、地点、直接引语都必须有 evidence entry；无法映射即 fail。
4. 直接引语只能来自字幕/可靠逐字来源。默认文案要求至少 5 条人物直接引语；若源素材客观不足 5 条，必须明确标记 `insufficient_quotes` 并 block 或取得用户批准，绝不编造。
5. 每个正文段落至少关联 1 个 evidence id；纯转场段最多 2 段，且每段不得超过 100 个非空白字符。
6. 禁止从同批其他目录复制事实、人物、数字、引语或叙事段落作为当前目录的填充材料。

### 18.3 单目录最终文案硬验收 Gate

只有下面所有条件同时通过，才允许把 draft 原子提升为 `爆款口播稿.txt`：

1. **字数**：`len(re.sub(r'\s+','', text))` 必须在 4500–5500；4499 或 5501 都是不合格，不允许“接近即可”。
2. **完整 Humanizer**：必须执行 bundled `vendor/blader-humanizer/SKILL.md` Embedded 模式的 draft → AI-pattern audit → fabrication audit → final rewrite 全流程；只说“已去 AI 味”不算证据。
3. **事实复检**：Humanizer 后重新对 evidence/SRT；新增或改动的事实也必须重新映射。发现 1 条无法溯源的具体事实即 fail。
4. **禁 AI 套壳**：现有 `BANNED_PATTERNS` 命中数必须为 0，并扩充检查「值得注意的是」「不可否认的是」「综上所述」「总的来说」「本质上」「核心在于」「底层逻辑」「真正重要的是」「我们可以看到」「接下来让我们」「这不仅是…更是…」等高频壳句。
5. **禁止空洞排比**：连续 3 个同构句、三段式抽象总结、无新事实的“意义升华”均 fail；validator 可以启发式报警，最终必须由独立 reviewer gate 再判一次。
6. **段落有效性**：不得存在仅为凑字数的重复解释；相同规范化段落重复出现 2 次即 fail；连续重复句或高相似句必须修复。
7. **口播性**：不得含制作指令、章节标题、审校说明、模型自述、路线提示；最终文件只能是主播可直接读出的正文。
8. **风格 profile**：默认 AutoYY 执行第三人称/SRT-first 规则；`laorou` profile 才允许其第一人称口癖与固定签名。不同 profile 不得混写。
9. **完成状态**：只有 `drafted -> humanized -> fact_checked -> quality_validated -> approved` 全部成功，状态才可为 `complete`。

### 18.4 跨目录反模板 / 反批量糊写 Gate

新增批量质量校验器（建议 `scripts/validate_voiceovers.py` 或公共模块等效入口），对本批所有最终稿两两比较。以下任一命中，相关目录全部退回重写：

1. 两篇不同选题正文出现**规范化后连续 ≥80 个字符完全相同**的片段 → hard fail；仅允许配置白名单中的固定签名/法定固定语句。
2. 长度 ≥80 的段落，两篇稿件 `SequenceMatcher`/等效相似度 ≥0.88 → hard fail。
3. 两篇稿件前 200 个非空白字符相似度 >0.72 → hard fail，防止统一模板开场。
4. 两篇稿件后 200 个非空白字符相似度 >0.72 → hard fail；profile 明确规定的固定签名先剔除再比较。
5. 任意两稿由 ≥30 字 matching blocks 计算出的共享正文字符占较短稿比例 >8% → hard fail。
6. 同批标题完全重复、只是替换一个名词/数字的机械模板标题 → fail；发布信息必须逐 topic 独立生成和验证。
7. 不允许维护“通用过渡段库”向每篇稿件填充固定 100–300 字转场；需要过渡时必须贴合当前 evidence。

这些阈值属于 release gate，不允许 agent 为了让当前批次通过而临时调高；调整阈值必须单独提交、带测试数据和维护者批准。

### 18.5 双重验收：机器 Gate + 独立语义 Reviewer

批量规模 >=2 时，每篇稿件必须经过两道互相独立的检查：

**Gate A：确定性机器检查**
- 字数、文件名、空文件、禁词/禁壳、重复段落、跨稿重复、直接引语计数、evidence completeness、profile 规则、状态机全部自动检查。
- Gate A 任一 hard fail 时不得进入 approved，也不得让 reviewer 用主观判断“放过”。

**Gate B：独立语义 Reviewer**
- reviewer 使用新的上下文，只读取当前 topic 的 SRT/evidence、style/profile 和候选 final，不读取其他 topic 的成稿，不沿用 writer 的自我评价。
- 必查：是否像同一素材讲出来、是否凭空补因果/心理/结论、是否出现明显 AI 套路、是否为了字数重复解释、开头钩子是否在后文兑现、引语是否自然且来源真实。
- reviewer 必须输出结构化 `pass/fail + reason_codes + evidence`；“总体不错”“基本达标”不能代表 pass。
- reviewer 发现任何 unsupported fact、明显错人物/错事件、整段模板腔 → hard fail。

自动修复最多进行 3 个 writer→Humanizer→Gate A→Gate B 循环。第 3 次仍失败时状态固定为 `blocked_quality`，整批 exit 1，交给人工处理；禁止无限重写，也禁止降低门槛。

### 18.6 Hermes / Workbuddy 批量调度规则

1. 收到多个子目录时，先 inventory，再生成显式 queue；禁止把全部 SRT 拼成一个超长 prompt 后一次生成。
2. queue item 必须记录 `topic_id/source_hash/style_profile/status/attempt/reason`，每次只锁定一个 writer item。
3. writer 完成后立即验证当前目录；当前目录未 pass 前，可以继续处理其他独立目录，但不得把失败项标为 done。
4. 批量汇总只读取各 topic 的 gate 结果，禁止 agent 凭“文件都生成了”判断完成。
5. 上下文压力过高时必须切新 worker/context，而不是减少 SRT 阅读量或压缩成未经核验的模型摘要。
6. source 摘要只能作为导航；最终事实和引语仍必须回到原 SRT/evidence。
7. 任一 agent/sub-agent 报告 `complete` 时，必须附带机器 gate 生成的结果文件或结构化字段，不能只用自然语言自报完成。

### 18.7 每批次强制验收表

批量交付时必须逐目录输出至少以下字段：

```text
topic | chars | srt_full_read | evidence_items | quote_count | humanizer_pass | fact_check | banned_hits | cross_copy_pct | reviewer | status
```

总体验收条件：
- 每一行 `chars` ∈ [4500, 5500]；
- `srt_full_read=true`；
- `humanizer_pass=true`、`fact_check=pass`、`banned_hits=0`、`reviewer=pass`；
- `cross_copy_pct <= 8%` 且没有 18.4 的其他 hard fail；
- 所有 topic status 都必须是 `approved/complete`。

有任意一行不是 complete，批次结论只能是 `INCOMPLETE` 或 `BLOCKED`，绝对不能写“总体完成”。

### 18.8 必须实现的代码与回归测试

本节不能只停留在 SKILL 提示词。Codex 必须把关键门槛实现为代码和 CI：

- 新增统一 voiceover validator：检查字数、禁壳、段落重复、profile、evidence 完整性、quote/evidence 关联和最终状态。
- 新增 batch cross-copy validator：执行 18.4 的跨稿比较，并提供 JSON 输出与非零退出码。
- writer/promote 流程只有在 validator + reviewer 均 pass 后才允许原子替换 `爆款口播稿.txt`。
- `SKILL.md`、workflow-spec、tool-map 同步写明本节规则，Hermes / Workbuddy / Codex 读取同一套契约。
- 不采用“AI detector 概率分数 > X”作为唯一 gate；这类分数不稳定。以确定性结构规则、source grounding、Humanizer 和独立语义 review 组合验收。

测试必须至少包含：
1. 4499 字、5501 字 → fail；4500/5500 边界 → pass（其他条件满足时）。
2. 含禁用 AI 壳句与空洞三连排比 → fail。
3. evidence 中没有对应条目的数字/日期/直接引语 → fail。
4. 两篇不同 topic 复制同一段 ≥80 字 → 两篇都 fail。
5. 两篇机械复用开头/结尾超过阈值 → fail。
6. Humanizer 状态缺失或只是自然语言声称执行过 → fail。
7. reviewer fail 后即使机器项全绿也不得 promote。
8. 3 个目录中 2 个 pass、1 个 fail → batch exit 1，汇总必须显示 `2 complete / 1 failed`，不能 exit 0。
9. 修复失败目录后 resume 只重跑失败/stale 项，已 approved 的两个目录 hash 不变。

### 18.9 新增高优先级 Issue

| ID | 级别 | 问题 | 完成判定 |
|---|---|---|---|
| YY-P0-007 | P0 | 多目录写稿缺少逐 topic 强制质量 gate，agent 可整批偷懒 | 18.1–18.7 全部代码化并有 3-topic 回归测试 |
| YY-P0-008 | P0 | final 文件提升未强绑定 Humanizer + fact check + reviewer | 未通过任一 gate 时绝不生成/覆盖 final |
| YY-P0-009 | P0 | batch 可在部分 topic 失败时仍自然语言宣称完成 | 任一 topic fail → process exit 1 + batch INCOMPLETE |
| YY-P1-014 | P1 | 缺少跨 topic 复制/模板化检测 | 18.4 阈值实现并覆盖坏 fixture |
| YY-P1-015 | P1 | 缺少逐段 source/evidence 追踪，容易编造补字数 | evidence ledger + unsupported fact gate 生效 |
| YY-P1-016 | P1 | 多 topic 共用超长上下文会导致后续目录质量衰减 | writer 单 topic 独立上下文，默认 concurrency=1 |

**Release 追加条件**：第 17 节 Definition of Done 必须同时满足本节全部 P0/P1；18.8 的 multi-topic quality regression suite 未全绿时，禁止创建 release tag，也禁止对 Hermes / Workbuddy 宣称“批量稳定”。
