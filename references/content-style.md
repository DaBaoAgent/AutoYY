# Content and cover style

## Contents

- Voiceover objective
- Voiceover structure
- Language rules
- Prohibited patterns
- Mandatory Humanizer pass
- Publication information
- Topic covers
- Collection covers
- Cover quality control

## Voiceover objective

Write text that can be pasted directly into text-to-speech or read by a narrator. Sound like a knowledgeable friend telling a surprising true story, not a paper, editing brief, lesson plan, or AI outline.

Default target: 4,500–5,500 non-whitespace Chinese characters for a roughly 15-minute finished piece. Adjust to narration speed and requested duration. Substance has priority over length.

## Voiceover structure

- Open within 80–150 characters with danger, contradiction, scale, or a result that demands explanation.
- Establish the central question quickly.
- Move chronologically or causally; introduce one idea per paragraph.
- Renew curiosity every 500–800 characters through a concrete discovery, consequence, or reversal.
- Explain numbers by comparison when useful.
- Resolve the opening question and connect the ending to people, choices, or present-day meaning.

Do not announce structural beats. The listener should feel them, not hear their labels.

## Language rules

- Prefer short and medium sentences that are easy to say in one breath.
- Use everyday verbs and concrete nouns.
- Explain specialist terms immediately.
- Rewrite tongue-twisters, stacked modifiers, and dense abstractions.
- Use cautious wording for disputed claims.
- Keep names, dates, quantities, and causal statements consistent with checked sources.

## Prohibited patterns

Do not include:

- `【开场钩子】`, `【核心悬念】`, `【推进】`, `【结尾升华】`, or `【补充叙事】`;
- "镜头切到""画面来到""字幕里"等 editing directions;
- "这段视频最容易省略的一层""回看整条因果链""前面三条线索共同指向"等 meta-commentary;
- generic filler copied between topics;
- repeated complete paragraphs;
- unrelated systems or engineering language inserted into an animal, history, or culture topic;
- "点赞、关注、收藏、转发"等 calls to action unless explicitly requested;
- fabricated dialogue, motives, facts, numbers, or conclusions;
- 三连排比式空洞总结段（排比空洞段）：以抽象名词清单开头（如「改革涉及人员培训、医疗服务、监控、问责、保释制度…」），接三个同构排比否定句（「增加A可能帮助X，却不能替代B；惩处C可以追责，却不足以修复D；关闭E也不会自动消除F」），再以升华句收尾（「真正改革必须从…前提出发」「X的本质是Y」）。这种段落全部由抽象名词和句式堆砌而成，没有任何具体的人、事、数字，一听就是 AI 凑字数。出现即改写：保留真实信息点，改成具体的人怎么被影响、具体的事怎么发生、口语化的说法；信息不足就删短，不要硬凑。

## 叙述视角（第三人称规则）

- 叙述层一律第三人称：讲述者不出现「我」「我们」等第一人称（如不说「我们拿到一段录像」「我们可以看到」），改用客观转述（「有一段监控录像」「记录显示」）。
- 人物引语例外：当事人的原话（字幕里的直接引语）保留第一人称，如「我需要帮助」「我不管同情这回事」。引语是人物说的，不是叙述者说的，必须原样保留。
- 优先采用字幕原文引语：重写文案时，把 srt 里的真人原话挑出来放进文案（人名说：「…」），这是纪录片文案最有人味的部分，比任何转述都强。原话缺失时才用间接转述（他说，…）。
- 每篇文案至少包含 5 条以上人物直接引语（从 srt 提取）；引语与事实一样不可编造，只能来自字幕。

## Critical rewrite rule: SRT-first approach

**When srt/subtitle files are available, they must be the primary source for voiceover writing—not supplementary context.** Do not use srt as a fact-checking tool after drafting from memory or summary. This prevents AI hallucination and ensures factual grounding.

Process:
1. Read the full srt text (extracted without timestamps) before drafting.
2. Use srt as the narrative backbone—every major plot point, quote, and detail should come from srt.
3. If srt is insufficient (gaps, missing context), supplement ONLY with verified facts from the user or trusted sources, never with AI-generated filler.
4. Prioritize direct quotations from srt—real people's words are more compelling than AI paraphrases.
5. When humanizing, preserve all srt-derived facts, names, dates, and quotes exactly as they appear.

Example: In the Rikers Island documentary, the original draft contained abstract analysis ("制度一旦贴上有罪标签…") that was completely absent from srt. The rewrite used only srt facts: Ballard lying in his own waste, Ford getting a $20 gift card, Stroud saying "I'm not in the compassion business." These concrete details from srt made the story human.

Do not use a fixed transition library to expand every script. When the source is thin, find another reliable source or shorten the piece.

## Mandatory Humanizer pass

Every generated or revised voiceover must pass `vendor/blader-humanizer/SKILL.md` in Embedded mode before it is saved as final. Run its draft, AI-pattern audit, fabrication audit, and final-rewrite loop internally. The output file must contain only the final spoken Chinese text.

During humanization:

- preserve all source-supported facts, names, dates, quantities, quotations, and causal claims;
- preserve the user's approved voice and useful specific details;
- remove formulaic signposting, inflated significance, promotional wording, forced groups of three, false contrasts, synonym cycling, generic conclusions, manufactured punchlines, and repetitive transitions;
- vary sentence length naturally without turning the script into stacked dramatic fragments;
- remove em and en dashes unless an approved user writing sample clearly requires them;
- never add a fact merely to make a sentence sound more human.

Apply the bundled Humanizer patterns to Chinese as well. Remove or repair these high-priority shells when they are padding rather than necessary meaning:

- 「不是A，而是B」「并非A，而是B」「不只是A，更是B」「与其说A，不如说B」;
- 「先A，再B」「第一步…第二步…」when the order does not change the outcome;
- 「真正重要的是」「本质上」「核心在于」「底层逻辑」;
- 「接下来」「我们可以看到」「值得注意的是」「不可否认的是」「总的来说」「说白了」「划重点」;
- repeated 「观点：解释」paragraphs, mechanically even paragraph lengths, and three or more clauses with identical grammar;
- generic ending questions such as 「你觉得呢？」 or 「是不是很震撼？」 unless the user explicitly requests a specific comment hook.

After the pass, compare the final script with the source material and read it aloud. Repair awkward pronunciation, unsupported additions, factual drift, and accidental omissions. If substantial material is added after this check, run the Humanizer pass again. A script that skips this gate is not final.

Keep versioned drafts during revision. After the approved text has been promoted to `爆款口播稿.txt`, read it back and confirm it is non-empty and still passes factual, length, speakability, and Humanizer checks. Then delete superseded voiceover files in that topic directory, including `爆款钩子文案.txt` and versioned `爆款口播稿-*` drafts. Keep the drafts when promotion or validation fails. Do not treat subtitles, research notes, publication information, or unrelated text documents as disposable drafts.

## Publication information

Use exactly:

```text
<title of at most 25 characters>
#标签1 #标签2 #标签3 #标签4 #标签5
```

Write exactly two non-empty lines. Do not add `标题：`, `爆款标题：`, `标签：`, section headings, blank lines, emoji, publishing advice, platform notes, cover copy, or interaction prompts.

Count every Chinese character, digit, Latin letter, punctuation mark, and space toward the 25-character title limit. The title should follow Douyin's high-click logic while remaining factual: build an information gap with a checked number, contrast, consequence, or concise question. Prefer a concrete causal question or measurable scale over a proper noun alone. Never fabricate or exaggerate beyond the source.

Put exactly five space-separated, topic-specific hashtags on the second line.

## Topic covers

Use `assets/topic-cover-3x4-approved.png` and `assets/topic-cover-4x3-approved.png` as the approved style references.

- Ratios: 3:4 portrait and 4:3 landscape.
- Background: photorealistic documentary image tied to the exact topic.
- Type: rough handwritten Chinese brush lettering with strong contrast.
- Main title: exactly six Chinese characters, one line.
- Subtitle: exactly eight Chinese characters, one line.
- Placement: upper half.
- Width: subtitle about two-thirds the main-title width.
- Main-title treatment: extra-large vivid golden-yellow brush calligraphy, thin crisp black outline, and strong soft black drop shadow; nearly span the available safe width.
- Subtitle treatment: medium-large white brush calligraphy, thin crisp black outline, and strong soft black drop shadow; center directly below the main title.
- Keep both lines visually centered with clear spacing and safe margins; do not crop a stroke.
- No extra text, English, logo, watermark, or decorative badge.

## Collection covers

Use `assets/collection-cover-1x1.png` and `assets/collection-cover-4x3.png` as style references.

- Ratios: 1:1 and 4:3.
- Background: one iconic photorealistic scene expressing the collection.
- Title: exact four-character collection name, one centered line.
- Type: warm antique-gold rough brush calligraphy with a subtle dark shadow.
- No subtitle or other text.

## Cover quality control

1. Visually read every Chinese character.
2. Confirm no unwanted words or pseudo-text.
3. Measure pixel dimensions and aspect ratio.
4. Confirm the scene is realistic and topic-relevant.
5. Compare both variants for consistent identity.
6. Save drafts with versioned names; promote only approved files to final names.
