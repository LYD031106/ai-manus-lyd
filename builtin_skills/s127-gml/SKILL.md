---
name: s127-gml
description: >-
  把中国海事主管机关的法规文件（交通管理规定、VTS 服务指南、船舶定线制与报告制、
  商渔船碰撞高风险警示区、沿海公共航路、渔船推荐航路、桥区水域、船舶避险推荐水域等）
  转换为符合 IHO S-127 产品规范的 GML 数据集。当用户提供海事法规 PDF/DOC/DOCX、
  坐标附件（CSV/XLS）或提到 S-127、MTM、要素编码、GML 数据集、127CN00 命名、
  智慧航保值班子系统导入时使用。不负责 ENC/S-57 海图本身的编辑。
---

# S-127 海事数据 → GML 数据集

把「一组法规文件（多个 PDF + Word + 坐标附件）」变成「一个可导入智慧航保服务系统的 S-127 GML 数据集」，
外加一份《S-127要素生产与检查记录表》和一份关联类型补录清单。

三层依据，分工清楚：

| 层 | 来源 | 回答什么 | 落在哪 |
|---|---|---|---|
| **schema** | `schemas/S127FC.xml`（IHO，v1.0.1-20190628） | 允许什么：要素类、子元素顺序、必填/可选、多重性、枚举、几何类型 | `scripts/s127_catalogue.py`（自动生成，勿手改） |
| **约定** | 《S-127数据生产与发布操作指南（修订版）》 | 约定怎么填：双语、命名、来源、不表达清单 | `scripts/s127_model.py` + `references/` |
| **实证** | 13 个已通过二审并入库的真实数据集 | 生产上实际怎么做：枚举 code 的数值、CARIS 版式 | 已并入上面两层，可用 `--verify` 复核 |

要素目录换版后重跑 `python3 /opt/skills/s127-gml/scripts/gen_s127_catalogue.py` 即可，不要手改生成物。

## 结果表格格式

回复中的要素结果表及导出的 Excel、CSV 统一使用以下八列，列名和顺序固定，每个要素一行：

| 要素类 | FIN | 中文名称 | 英文名称 | 编码信息 | 有效期起 | 有效期止 | 来源 |
|---|---|---|---|---|---|---|---|

地理要素 FIN 与本次构建的 GML 编号一致（如 `2225-1`），信息要素显示 `---`。
编码信息采用 `字段=代码(枚举标签)`（如 `restriction=3(fishing prohibited)`），多项以分号及单元格内换行分隔；
非枚举值保留实际内容。有效期完整日期使用 `YYYY-MM-DD`，缺少依据的名称、日期和来源留空。
Excel 使用蓝底白字表头、正文自动换行和细边框。字段取值、公共属性继承、来源批注及完整格式规则见
[09-UML结构与文本抽取规则.md §8.5](references/09-UML结构与文本抽取规则.md#85-结果表格)。

## 核心原则：不要手写 GML

GML 的命名空间、几何图元链、`gml:id` 串联、`boundedBy`、`xlink:arcrole`
一处写错整份数据集就废了。**本技能的分工是：你负责判断与编码，脚本负责序列化。**

```
法规及附件 → 解析与编码 → 初始 GML、要素清单 JSON、几何报告
同名准确几何 GML + 要素清单 JSON → 补录并重建完整 GML
待审的最终 GML → 数据标准与编码检查报告
```

GML 一律由脚本生成。根据上传件和用户意图选择阶段，可在现有聊天中分多轮完成。
用户上传 GML 用于补录时执行阶段二，上传 GML 要求审查时执行阶段三。多份草稿并存时先确认对应数据集。

已有结构化成果时，用户可上传文件并发送 `S-127 /draft`（一个 featureset JSON）、
`S-127 /complete`（一个 featureset JSON，几何待补时另传一个准确几何 GML）或
`S-127 /check`（一个 GML，可同时传阶段二 JSON 核对原文位置和待复核项）。
这三个入口直接运行脚本并交付附件。阶段二可在命令后追加 `--match 草稿ref=上传GML的gml:id`，逐个明确同名分段或经确认的异名对应。
正式发布验收用 `S-127 /check --require-coverage`。只有交付方确认要使用项目兼容码时，三个阶段才追加 `--allow-restriction-3`。

源文件首次编码时，先把已查证的名称、条款、属性和关联写成可校验的 JSON 并构建草稿。几何可在保存检查点后逐项补齐，单项坐标处理失败时保留该要素，空几何并说明原因。已有草稿即先交付，记录表和长篇复核随后处理。

## 要素清单先定稿（按公告逐条列举）

**在写 `featureset.json` 之前，先按公告/附件逐条列出 `/home/ubuntu/要素清单.md`：**

清单使用上面的八列表格，每个独立要素占一行。构建前尚未确定的地理要素 FIN 留空，构建后回填实际值；
几何类型、按公告逐条核对的数量及条款证据保留在清单附注中。

硬规则：

1. **数量/类型/名称由公告和附件决定。** 公告分别命名的水域、桥梁、航段、掉头区、锚地、警戒区、报告类型，逐条建；公告明确写“整体划定/合并为一处”的才合并。不要凭经验默认增删。
2. **不得默认新增信息要素。** Authority、Applicability、ShipReport、ContactDetails、Regulations、ServiceHours 只有公告/配方明确要求时才建；不要因为“规则上更完整”就额外增加。
3. **不得把公告分别命名的长航段、多桥、多报告类型合并。** 公告没有说“整体一处”的，不要自行合并；公告没有区分的，也不要自行拆分。
4. **名称用公告/附件原名。** 保留原名中的海域、港口和航路段；原名没有的地名前缀不添加。
5. **几何类型按原文和要素目录。** 原文给出完整边界，或给出中心线和能唯一推导边界的宽度、偏移及分段规定时，构建 `surface`。原文本身只定义中心线且目录允许时用 `curve`。原文定义的是水域但边界不全时登记待补录。
6. **清单与公告逐条对齐。** 数量对不上时回到公告核对，不要套用固定数字；清单定稿后再写 JSON。

### 缺坐标/缺附件的处理

- 公告写“详见附件”但附件未提供时：先明确缺什么，不要用 OSM、示意图或网页检索无限反推。
- 确实没有坐标时：建要素并保留属性及关联，几何留空。只有概位或勾绘结果时，设置 `"geometryStatus": "review"`。
  阶段一交付初始 GML（没有任何坐标也可生成）、完整要素清单及记录表，并请用户提供同名准确几何。
  列出受待补录几何影响的关联，包括 VTS 区域至规则要素的关联。
  不要以附件中的示意图、航标点、通航净宽或少量可读坐标替代真实边界。法规完整规定的圆形范围用圆心和半径构建；不要用少数拐点代替圆。
- 单步工具调用控制在 **30–50 次**；解析、OCR、坐标、构建分步落盘，迭代将尽时优先交付已完成部分，不要直接耗尽 100 次。

### ① 解析：文本走脚本，图走模型

**核心原则：有文本层的文件，文本一律本地抽取。** 文本层是无损的，过一遍 OCR 只会变差
（实测模型会把原文 `宽32.3米、、满载吃水` 顺手改写成 `宽32.3m`，法规数据不能这样）。
**只有图里的信息才交给模型。**

先无脑跑一遍 `parse_office.py`，它自己判断类型并在输出里告诉你还需不需要调工具：

```bash
S=/opt/skills/s127-gml/scripts

python3 $S/parse_office.py /home/ubuntu/upload/*.pdf /home/ubuntu/upload/*.docx \
        /home/ubuntu/upload/坐标.csv \
        -o /home/ubuntu/解析-local.md --image-dir /home/ubuntu/图件
```

| 情况 | 脚本做什么 | 还要不要 parse_regulation |
|---|---|---|
| PDF 有文本层、无大图 | 抽文本 + 表格 | **不要** |
| PDF 有文本层、有大图 | 抽文本 + 表格，含图的页渲染成 PNG | 要，**只读导出的 PNG** |
| PDF 无文本层（扫描件） | 整份逐页渲染成 PNG | 要，**只读导出的 PNG** |
| Word 有内容图 | 抽正文 + 表格，导出大图 | 要，只读导出的图 |
| Word 只有装饰图 / Excel / CSV | 全部抽完 | **不要** |

读 `解析-local.md` 里的 `>` 提示行 —— 脚本会明确写出「已导出以下图片，请用
parse_regulation 解析」或「全为装饰图，无需调用」。图片按 ≥150×150 过滤，
公文里的印章碎片、页面图标可在没有资料内容时过滤；涉及边界、坐标或条款的图件应保留检查。

```
# 只传上一步导出的 PNG —— 本工具只收图片，PDF/Word 传进去会被拒
parse_regulation(files=["/home/ubuntu/图件/成山角定线制_p8.png",
                        "/home/ubuntu/图件/附件2示意图_img1.png",
                        "/home/ubuntu/图件/扫描通告_p1.png"],
                 output="/home/ubuntu/识别-图件.md")

# 重点关注坐标（仍是原样转录，不做换算）
parse_regulation(files=["/home/ubuntu/图件/坐标附件_p1.png"], coords_only=true,
                 output="/home/ubuntu/识别-坐标.md")
```

`parse_regulation` 参数：
- `files`（string[]，必填）：绝对路径，**只接受图片** .png/.jpg/.gif/.webp/.bmp
- `output`（string，必填）：输出文件路径，绝对路径
- `coords_only`（bool，可选）：提示模型重点关注经纬度，仍按原样转录
- `model`（string，可选）：覆盖默认模型

> **它做的是文字识别，不是结构化。** 返回的是图中原文（度分秒也保持原样），
> 用的是 `qwen-vl-ocr` 而非通用视觉模型 —— 后者实测会改写原文：
> 「采取**避让**行动时」→「采取让行动时」、「**船载**自动识别系统」→「船舶自动识别系统」。
> 法规条文不能被改写，所以识别这一步刻意不让模型归纳。
>
> **分要素、判专题、把度分秒换算成十进制，都是你拿到原文之后自己做**
> —— 那一步有原文可核，比让识别模型顺手总结安全。坐标换算用 `coords.py`，不要手算。
>
> 单页输出上限 4096 tokens。若输出里出现「本页输出已达上限」的提示，
> 说明该页没识别完，把那页图裁成上下两半重新识别。

两条路的产出都要读，合起来才是完整素材。`.doc` / `.wps` 两边都不支持，先转 `.docx`。

### ②~⑤ 后续步骤：调用本地脚本

```bash
S=/opt/skills/s127-gml/scripts

# ② 你阅读 解析结果.md，生成 featureset.json

# 阶段一：校验并构建 127CN00XXX001.draft.gml，列出全部要素及几何状态
python3 $S/geometry_workflow.py draft featureset.json \
       -o 127CN00XXX001.gml --featureset-out featureset-draft.json \
       --report 几何清单.md --assoc-csv 关联补录清单.csv --record S-127草稿记录表.xlsx

# 阶段二：按名称补录用户上传的准确几何
python3 $S/geometry_workflow.py complete featureset-draft.json 准确几何.gml \
       -o 127CN00XXX001.gml --featureset-out featureset-complete.json \
       --report 几何补录报告.md --assoc-csv 关联补录清单.csv --record S-127补录记录表.xlsx

# 阶段三：检查用户上传的最终 GML
python3 $S/geometry_workflow.py check 最终.gml --report 编码检查报告.md

# 阶段一、二用各自对应的 featureset JSON 出记录表
python3 $S/make_record_table.py featureset.json \
       -o S-127要素生产与检查记录表.xlsx
```

阶段一先向用户交付草稿、校验后的 featureset JSON 和几何清单，说明草稿供审核。记录表随后生成。
阶段二沿用对应 JSON，从同名的 S-127 GML 补入准确几何，并保留已编码的属性和关联。
同名多段须在清单中分配独立 ref；确认对应关系后用 `--match 草稿ref=上传GML的gml:id`，可重复传入。
只有同名、没有独立位置依据时先保留供体候选并标记待确认。人工核对后用 `--match` 明确指定；即使明确指定，有独立锚点且位置冲突的候选仍不能放行。
仍有缺失、歧义、待确认或几何类型不符时继续交付草稿和报告。用户实际确认过的候选可填写
`geometryEvidence.reviewReference`（能查到的复核记录），再移除 `geometryStatus` 标记；
智能体不得替用户编造确认记录。全部几何齐全且已确认时可直接运行阶段二，省略几何 GML 参数。
跨会话补录时，请用户重新上传阶段一的 JSON。

阶段三分列目录和结构问题、编码告警和人工核对项。提供完整产品 XSD 及其依赖时用
`--xsd /path/to/S127.xsd` 校验；缺少完整 XSD 时在报告注明未执行。
用 `check 最终.gml --source-featureset 阶段二结果.json --report 检查.md` 复核独立锚点及尚未解决的原文问题。
提供 `DataCoverage` 时校验其覆盖面的并集是否覆盖完整地理要素几何，包括孔洞和越界部分。生产发布检查追加 `--require-coverage`，缺正式覆盖面也判定为阻断。
只有 GML 时，只能作文件内的检查，不能据此说原文位置、条款适用性或范围已验收。
默认按 S127FC 目录；航行限制区的禁捕捞条文保留在文字中。只有项目明确要求兼容码 3 时，才在三个阶段一致传入 `--allow-restriction-3`；检查报告仍记录目录差异。

### 根据原文记录编码依据

按以下顺序处理每批新文件：

1. 先把文件分为**原文依据**（现行公告、附件、正式海图）、**几何供体**和**验收对照**。人工成品、反馈样表、上一次的输出放入最后一类；它们不提供本轮名称、数量、日期、码值或关联结论。供体仅用于阶段二，不能反填为独立原文证据。
2. 只从原文记录事实：适用对象、地理形态与范围、时间或阈值、行为、条件和例外。逐条独立选择要素、字段和关联；引用外部法规时核对其适用对象、生效、失效与续期。仅提到某名称或引用某文号，并不能证明全部条款适用。
3. 写出原文依据和未确认项，再生成 GML。冻结第一阶段清单后才对照人工成果；发现差异回查原文和规则。如果原文无法支持改动，保留差异供人工核查。不能为了让某个已知成果一致而加入名称、文号、编号、码值或数量的特判。
4. 地理清单完成后，回扫正文中按水域类别统一施加的条款。按附件中的成员关系，将限制分配给确实适用的每个要素 ref。核对每个要素的完整限制集，并在根级 `restrictionClauses` 记录原文出处、证据、作用范围和目标。原文分段独立核对，同名和相邻关系均不能证明条款适用。

用以下 JSON 辅助字段保存出处（脚本不会把这些工作字段写进 GML）：

| 输入字段 | 用法 |
|---|---|
| `sourceBasis.kind` 与 `sourceBasis.reference` | 先判定条文用途，记下文件与条款或表格行号。受限普通航段用 `regulated-fairway`，规章明确的桥区用 `bridge-waters`，桥梁信息通告用 `bridge-information`；按原文填名称、实际限制和例外。报告义务用 `reporting-obligation`；明确规定的航路措施才用 `routeing-measure`，并填原文类别。 |
| `restrictionClauses` | 每项写 `reference`、`evidence`、`scope` 和明确适用的 `targets`（要素 ref）。全域、全时段、且无对象条件或例外的限制用 `area-wide`，同时填 S-127 `code`。船型、天气、时段、许可或例外用 `conditional`，仅覆盖要素部分范围用 `partial`；用 `regulation` 指向保留完整条件的规则要素，并由地理要素用 `theRxN` 关联。局部范围能够独立成面时优先使用该子区域。引用效力或适用范围待证时用 `unverified` 并在 `evidence` 写明缺什么。 |
| `sourceBasis.verticalClearance` | 仅桥梁信息通告需要。逐项填写原文净空高度、基面及适用桥孔，例：`[{"zho":"通航净空高度 18 米（当地高程基面）"}]`。未提供留空。净宽、设计船型等不混写在桥区净空信息中。 |
| `sourceBasis.partitionGroup` | 只有条文或海图明确这些面彼此独立才给同组标识。数量和范围按这次输入的通航孔、桥轴线、岸线或分界依据确定。不能靠名称、FIN、其他案例数量或附件行号推断。 |
| `reportedDateBasis` | 在 `sourceIndication` 的同级（要素或 `common`）记录发文日期出处。缺少出处的发布日期在初稿中留空；施行日、最后有效日、届满界点分开核对，不能用数据集日期填补。 |
| `validityBasis` | 在要素或 `common` 记录 `reference`、`effectiveDate` 以及有原文依据的 `lastValidDate`、`expiryDate` 或 `durationYears`。确认交付方的 GML `dateEndConvention` 后填 `last-valid-day` 或 `expiry-boundary`。记录表“有效期止”填最后有效日；报告同时列出届满界点。闰日周年或来源之间有歧义时待核对。 |
| `sourceBasis.originalName` | 原文明确给出要素中文名称时记录其原名，构建前按出处校正；只有上级地域或旧成果中的前缀不算名称出处。 |
| `sourceReview` | 在根对象或相关要素下记录尚未解决的问题，如 `[{"reference":"通告第二条引用的管理办法","reason":"期限届满，尚未核对续期或替代文件"}]`。引用效力或适用范围未核实，不把相关限制写成确定值；记录保留时只能交付草稿。可选字段正常缺失不用列为阻断。 |
| `geometryEvidence.kind` 与 `reference` | 完整边界用 `enc`、`source-boundary` 或 `user-gml`，注明版次、附表或来源 ID。圆形用 `source-circle` 并给 `center:[纬度,经度]` 和 `radiusMetres`。由断面切分、共边拼接或海图勾绘的候选用 `chart-trace`，保持待人工核查。没有出处的既有几何从流程草稿移除。 |
| `geometryAnchors` | 当原文或独立海图明确某点应位于要素范围内、线上或位置处时，填写 `position:[纬度,经度]` 和 `reference`。例如桥区内的桥址、圆形水域中心、报告线端点。仅以原文精度或项目正式口径填 `toleranceMetres`，不能为通过匹配随意放宽。超差不自动补录，有歧义仍请人工指定；没有独立原文坐标就报告这项限制。 |
| `coverageAreas` | 根级数组，每项记录 `geometry`（独立正式覆盖面的键）、`reference`（范围出处）和原文明确的显示比例尺。没有覆盖面依据时登记缺失，不能从要素外包框反造覆盖面。 |

草稿和补录阶段对 `restrictionClauses` 中列过的要素重算全区 `restriction`。有全区限制的普通航道和警戒水域可归为 `RestrictedAreaNavigational`。要素目录不支持的限制通过条款正文和关联保留，兼容码仅按本项目已确认的选项启用。缺少关联正文、适用范围或效力证据时列入待复核，补录几何后仍保留草稿状态。用生成后的清单执行 `check --source-featureset`，核对 GML 的全区码值。

法规条文规定的报告义务用 `Regulations` 或 `Restrictions`，按发文机构及目录区分；用 `InclusionType(included)` 绑定 `Applicability`，并由对应地理要素的 `theRxN` 关联。保留条款适用对象、时机和例外；原文明示的是报告服务或报告程序时仍可用 `ShipReport`。VTS 区域、报告线和独立报告点分别依据主管机关的范围定义；不把折线拐点当作报告点。只有坐标、岸线和地名都能核对时才闭合服务区。

船型、货物或作业条件限定的富余水深在 UCAA 填 `sourceBasis.kind=conditional-underkeel`。按原文写水深数值、计算依据和多值运算，并通过 `theRxN` 关联含正文和 `Applicability` 的规则。正文保留严格大于与大于等于的区别以及“散装”等限定。多个条件仅在原文逻辑一致时并入同一个 `Applicability`，混合 AND/OR 或目录无法表达时保留完整判定文字。

### 由供体生成待复核边界

当原文分段与人工 GML 不一一对应时，可用 `review_geometry.py` 生成候选；只处理不含内环的单面。一条原文明确断面可切成两个面；两个面只有在一条边完全重合时才拼接。操作计划是单独的 JSON 数组，例：

```json
[
  {"type":"split","donor":"f3","section":[[30.1,121.0],[30.2,121.0]],"left":"s1","right":"s2","reference":"附件 2 分界断面"},
  {"type":"stitch","donors":["f7","f8"],"target":"s3","reference":"附件 2 第 3 段与供体共边"}
]
```

`section` 按输入方向定义左、右，坐标顺序为纬度、经度。使用 `python3 $S/review_geometry.py featureset-draft.json 准确几何.gml 操作计划.json -o 待复核.json`。接着运行阶段二，从候选 JSON 生成复核草稿。报告列出断面端点与供体交点的大致差值；候选不通过最终检查，需用辅助图层核对或由用户给出准确 GML。缺少断面、航标、岸线或连接关系时直接列出缺项，不用包络或示意图跨越缺口。

辅助脚本：

```bash
python3 $S/gml_to_featureset.py 已归档.gml -o featureset.json   # GML 反解（存档同步）
python3 $S/roundtrip_check.py '示例/**/*.gml'                    # 保真度回归自检
```

坐标不要手抄，用脚本从原文或附件里提：

```bash
python3 $S/coords.py --text 通告正文.txt --close        # 从正文抓度分秒
python3 $S/coords.py --csv area.csv --group-col name --close   # 从坐标附件抓，按区域分组
python3 $S/coords.py "37°27′04″N 122°08′49″E"           # 单点换算
```

> 输出的坐标一律是 `[纬度, 经度]`，与 S-127 GML 的 `posList` 轴序一致。**不要调换**。

附件包含多组交错的边界或报告线时，先运行 `coords.py --text 原文.txt --indexed` 查看脚本提取的 1 基序号，逐项核对原文次序，再提供计划，例如 `{"g-area":{"type":"surface","rows":[3,1,2]},"g-report":{"type":"curve","rows":[6,7]}}`。运行 `coords.py --text 原文.txt --plan 计划.json` 得到 `geometries` 对象。也可换成 `--csv 原始坐标.csv`。序号只指该次提取结果，需逐点对照原文；计划不确定时保留待补几何，不另写会阻断整批要素的索引脚本。

## 七步工作流

### ① 解析源文件与识别专题
用户上传的通常是**多个文件**：通告正文 PDF、附件 Word（.docx）、坐标表 CSV/Excel、
示意图图片等。按上面 §① 的两条路分别解析：

```bash
# 第一步：所有文件一起丢给本地脚本，它抽文本并把该看的图渲染出来
python3 /opt/skills/s127-gml/scripts/parse_office.py \
        /home/ubuntu/upload/通告.pdf /home/ubuntu/upload/附件1.docx \
        /home/ubuntu/upload/坐标.csv \
        -o /home/ubuntu/解析-local.md --image-dir /home/ubuntu/图件
```

读一遍 `解析-local.md`，按里面的 `>` 提示行决定第二步 —— 若列出了导出的 PNG 路径，
把**那些 PNG**（不是原文件）交给 `parse_regulation`。

- PDF 有文本层：本地抽文本与表格；只有含大图的页会被渲染出来
- PDF 无文本层（扫描件）：整份逐页渲染成 PNG，文本全靠模型读
- Word (.docx)：本地提文本与表格；**跨列合并的表会带告警**，涉及坐标务必对照原文核对
- CSV / Excel：本地解析为 Markdown 表格
- .doc / .wps：不支持，先用 shell 转成 .docx 再解析

然后阅读解析结果 Markdown，确定覆盖的地理范围、发文层级和条文用途。
`references/05-专题配方.md` 提供判别条件和可用关联，按本次原文逐条采用；没有必要证据的要素不创建。
确认适用顺位、效力和替代关系：不能只按发布日从新到旧套用；必要时核对正式的有效目录和修订文件。

#### 多文件合并原则（核心规则）

> **无论源文件有几个，最终只生成一个 GML 数据集。**

多个源文件描述同一个管理体系（如：VTS 管理细则 + VTS 用户指南 + 报告制）时的处理：

1. **内容融合不拆分**：所有文件的内容合并到同一个 featureset.json 中，生成一个 GML
2. **出处追溯到条款**：每个要素的 `clause` 字段记录其来源——格式为 `《文件简称》第X条`
   或 `VTS指南 X.X`。多个来源用分号隔开
3. **几何取定位文件**：要素的 `sourceIndication` 填描述其地理位置/坐标的那份文件；
   其他文件的条文内容填进 `textContent`
4. **核对冲突**：按正式的适用顺位、效力和替代关系处理，无法确定时记入 `sourceReview`
5. **互补不重复**：不同文件对同一要素的描述应合并（如 A 文件给坐标、B 文件给规则），
   而不是建两个重复要素

### ② 抽取共同属性
文件头/尾一般给出全数据集共用的两组属性，放进 JSON 的 `common`：

- `fixedDateRange`：按来源得到施行日、最后有效日及届满界点，使用 `validityBasis` 记录推导依据和交付口径。
- `sourceIndication`：发文机构、文号、文件发布日期、发文类型。找不到发布日期时省略 `reportedDate`。
  `source` 优先使用原文或已核实的文号，缺文号时用正式文件名，缺件说明写进报告。

细则见 `references/01-通用编码规则.md` §2、§4。

### ③ 逐条转换为要素
对每一条条文问三个问题，顺序不能颠倒：

1. **要不要表达？** 常识性内容、海上作业申报、入港申请、指挥中心临时管制条件、
   桥区水域范围调整 / 代表船型变化等**范围元数据**一律不转换 —— 完整清单见
   `references/07-审核检查清单.md` §不表达清单。
2. **ENC 是否已表达？** 已表达的等同内容不转换为 S-127（指南 3.2(2)）。
   坐标与 ENC 一致的报告线/引航登离轮点，直接从 ENC 导入而不是重画。
3. **该建地理要素还是信息要素？**
   - 条文只作用于**单一地理要素** → 直接填该地理要素的 `textContent`，**不建信息要素**
   - 条文作用于**多个**地理要素，或需要**特定条件**（能见度/气象/水文）才成立
     → 建 Regulations / Restrictions 信息要素再关联
   - 直属海事局及以上发文 → `Regulations`；地方海事局及同级 → `Restrictions`

选择具体类前，先按候选类型阅读
[10-地理要素含义与适用范围.md](references/10-地理要素含义与适用范围.md) 或
[11-信息要素含义与适用范围.md](references/11-信息要素含义与适用范围.md)
的使用说明及对应类章节，核对要素含义、适用范围、排除情形和相近类区别。
再用 `references/02-要素字典.md` 核对必填项、几何和关联约束。几个最容易错的：
| 条文特征 | 正确要素 |
|---|---|
| 「禁止会遇」 | `WaterwayArea`（**不是**限制区） |
| 单向/双向通航等交通管制 | `RestrictedAreaNavigational` |
| 商渔船碰撞高风险警示区 | `ConcentrationOfShippingHazardArea` |
| 国内海事法规划定的桥区水域 | `RestrictedAreaNavigational`，按桥区适用条文填 `restriction`；桥梁信息通告的 `information` 只留净空高，见专题配方 §7 |
| 管理规定中的受限普通航道 | `RestrictedAreaNavigational`，按各航段实际限制编码；明确属于定线制、推荐航路、沿海公共航路或深水航路时才按对应类别建 `RouteingMeasure` |
| 富余水深，不同条件 | 每个条件各建一个 `UCAA`，即使几何完全相同 |
| 船舶避险推荐水域 | `PlaceOfRefuge` |
| 弯曲航段 / 弯道 | `RestrictedAreaNavigational`，`restriction=speed restricted`，命名 `No.N Curved Fairway Section` |
| 掉头区（限定船型） | `RestrictedAreaNavigational` + `permission`→Applicability，不填 `restriction` |
| 待闸锚地 / 枢纽水域限速 | `RestrictedAreaNavigational`，`restriction=speed restricted` |
| 禁鸣时段水域 | `RestrictedAreaNavigational`（`restriction` 不填，textContent 填时段） |
| 前后船安全距离 / 避让规则 | `WaterwayArea` + `dynamicResource=static` |
| 富余水深数值 | `UCAA`，在 `underkeelAllowance` 区分固定值与按船宽或吃水计算的值；`operation` 按原文的取值方式填写 |
| 内河航道通航安全管理规定 | 见 `references/05-专题配方.md` §10（内河航道选型） |

### ④ 编码属性
从原文抽取字段前，阅读 [09-UML结构与文本抽取规则.md](references/09-UML结构与文本抽取规则.md)，
按继承链、基数和复合属性结构组织数据，并结合 `references/08-字段抽取标注规范.md` 核对字段级要求。
`featureset.json`、证据和待补项的分离及项目字段映射见 09 §8；字段、枚举和基数仍以本技能采用的目录与 Schema 为准。

- 一律**先英文后中文**；复合属性里的 `language` 用 ISO 639-3（`eng` / `zho`）
- 单值文本属性写成 `["English text", "中文文本"]`，脚本会用 CR 连成一格
- `featureName` 写成 `{"eng": "...", "zho": "..."}`，脚本按规则补 `displayName`
- 枚举属性写 label（如 `"speed restricted"`），脚本从 XSD 查表补 `code`
- **承载原则（甲方确认）**：能用结构化属性（枚举 / 专用字段）承载的内容一律落结构化属性，
  不落自由文本；描述地理范围的文字一律不作文本属性（见 `references/01-通用编码规则.md` §5）；
  规则类条文优先映射成 `restriction` 枚举，只有无法枚举的合规内容才进 `textContent`
- 命名尽量精简，体现所属区域/系统与功能；英文词首大写，数值加千分位
- **只填原文有的**：XSD 必填项见 `references/02-要素字典.md` §二那张表，
  其余属性一律「有则填、无则不填」，不要为了"填满"而编造内容

禁止/限制类条文的常见 `restriction` 枚举映射（完整 39 值与 code 见 `references/03-枚举代码表.md`）：

| 原文 | `restriction` |
|---|---|
| 不得/禁止锚泊 | `anchoring prohibited` |
| 禁止捕捞 / 捕捞作业 | `fishing prohibited` |
| 禁止追越 | `overtaking prohibited` |
| 禁止掉头 | `turning prohibited` |
| 限速 / 最高航速 | `speed restricted` |

> 先分清普遍禁锚、带限定对象或时段的禁锚，以及经许可可锚泊。明确的普遍禁令可用
> `anchoring prohibited`，紧急避险或维护的例外另外保留；仅对特定对象或时段成立的，
> 保留适用条件和例外，不能扩写成区域普遍禁令。须经许可或仅指定锚位可锚泊时，按原文表达限制。

> `anchoring prohibited` 只在 `RestrictedAreaNavigational` 等部分要素类的 `restriction`
> 允许子集内；`RestrictedAreaRegulatory` **没有**这个取值（各要素类子集见 `references/08`）。

英译没有官方版本时：先搜权威机构的英文发文引用，再用工具翻译并逐句核对海事专业术语。

### ⑤ 建立关联
关联**非必须**，只在对用户有用时才连，避免冗余。

`references/04-关联关系.md` 有完整角色表、许可/包含类型选择规则、三方关联省略规则。
**哪个角色能挂在哪个要素类上由 XSD 决定**，写错位置校验器会直接报错并给出该要素类
允许的元素清单。

两个反直觉但重要的点：

- 指南说「所有地理要素必须关联唯一 Authority」，但 XSD 里**只有 11 个要素类有
  `controlAuthority`**。`RouteingMeasure`、`ConcentrationOfShippingHazardArea`、
  `UCAA`、`RadioCallingInPoint` 等根本没有这个关联，只能间接获得主管机构
  （`componentOf` 聚合到 VTSA/SRSA，或 `theRxN` → Regulations → `theOrganisation`，
  或在系统内关联）。
- `ShipReport` 用 `reportTo`（→Authority）和 `mustBeFiledBy`（→Applicability），
  **不是** `controlAuthority` / `permission`。

> ⚠️ **关联类型（required / included …）默认不进 GML**：CARIS 选不了，
> 13 个生产数据集也都没有。`build_gml.py` 把它们导出到 `--assoc-csv` 清单，导入系统后逐条补选。
> JSON 里照样要写 `"permission": "required"`，否则清单里就是空的。
> schema 其实支持用 `PermissionType`/`InclusionType` 对象承载类型
> （`--assoc-objects`），但生产系统没验证过 —— 想省掉补录工序请先拿小样试。

### ⑥ 生成与自检
按当前阶段运行 `geometry_workflow.py`。初始编码和补录时同时运行 `make_record_table.py`。
回复中的要素结果表与记录表保持相同的八列、要素顺序和字段值；完整规则见 09 §8.5。
ERROR 必须清零，逐条核对 WARN。即使 XML 可解析，仍要单列缺几何、概位和受影响的关联。
交付前对照 `references/07-审核检查清单.md` 走一遍。

#### 高频返工问题（来自真实审核记录）

以下是从已入库数据集的一审/二审意见中统计出的高频返工点，生成 JSON 前必须自查：

| 返工问题 | 正确做法 |
|---|---|
| textContent 与 ShipReport 内容重复 | 报告格式、报告内容、报告时机等**只通过 ShipReport 表达**，不在地理要素的 textContent 里重复 |
| Authority 重复创建 | 核实机构身份及适用范围；同一机构可复用，由各要素按实际监管关系关联 |
| ContactDetails 缺 language 属性 | 按来源提供的联系语言及实际渠道建记录，填写对应 `language`；确需为交付翻译说明时区分说明语言与受理语言 |
| 英文名称未首字母大写 | 每个实词首字母大写：`Ningde VTS Report Line`，非 `ningde vts report line` |
| VHF 内容写在 textContent 里 | VHF 频道填对应要素的 `communicationChannel` 属性或 `requirementsForMaintenanceOfListeningWatch`；详细通信信息填 ContactDetails 的 `telecommunications` |
| Applicability 分组错误 | 保留原文的船型、限制条件和 AND/OR；按语义和目录容量决定合并、拆分或复用，无法确认逻辑时列入复核 |
| 关联了不存在的系统实体 | GML 内部关联指向本数据集已有要素；确有等同系统实体且允许在目标系统内另行关联时，在记录表标明该安排 |
| 几何范围与文字描述不符 | 优先以 ENC 和海图为准勾绘；文字描述只作验证参考 |

### ⑦ 导入与存档
1. 值班子系统「导入GML」→ 核对导入数量
2. 按补录清单补选关联类型；补 `ContactDetails/radioCommunications`（Composer 不支持）
3. 沿海公共航路还要补编「所属海区」「航道类型」两个属性
4. 一审/二审意见改完后，**GML 与系统必须同步**，最终 GML 归档到共享文件夹

## 数据集命名

`127CN00` + 地域 + 专题 + 序号，8~17 字符，只允许字母/数字/下划线。

| 专题 | 缩写 | 示例 |
|---|---|---|
| 船舶报告制 | `SRS` | `127CN00ZJK_SRS_VTS001` |
| VTS | `VTS` | `127CN00NDVTS001` |
| 沿海公共航路 | `CPR` | `127CN00GD_CPR001` |
| 渔船推荐航路 | `FVRR` | `127CN00TJFVRR001` |
| 碰撞高风险警示区 | `PA` / `HCRA` | `127CN00TJHCRA01` |
| 船舶安全监督规则 | `SSSR` | `127CN00SZ_SSSR001` |
| 桥区水域 | `BA` | `127CN00XXBA001` |
| 避险推荐水域 | `WRVEW` | `127CN00XXWRVEW001` |
| 内河航道通航安全管理规定 | `<航道简称>` | `127CN00XXCANAL001` |

## 要素清单 JSON 结构

从空白骨架 `templates/featureset.skeleton.json` 开始，经核对后写入这次实际要素。
本技能的字段示意仅用于说明结构；生产字段、码值、关联和分片数量按本次有出处的事实生成。
字段约定：`schemas/featureset.schema.json`。S-127 权威 schema：`schemas/S127.xsd`。

```jsonc
{
  "dataset":  { "name": "127CN00XXYYY001", "updateNumber": 0, "agency": "CN" },
  "common":   { "fixedDateRange": {...}, "sourceIndication": {...} },  // 自动下发给所有专属要素
  "geometries": { "g-1": { "type": "surface", "exterior": [...] } },
  "features": [
    { "ref": "auth-1", "type": "Authority", "reusable": true,
      "featureName": { "eng": "...", "zho": "..." },
      "attributes": { "categoryOfAuthority": "maritime" } },
    { "ref": "por-1", "type": "PlaceOfRefuge",
      "featureName": { "eng": "...", "zho": "..." },
      "clause": "第三条",                                   // 只进记录表，不进 GML
      "textContent": [ { "categoryOfText": "extract",
                         "information": [ { "headline": { "eng": "...", "zho": "..." },
                                            "text":     { "eng": "...", "zho": "..." } } ] } ],
      "attributes": { "status": "recommended" },
      "geometry": "g-1",
      "associations": [ { "role": "controlAuthority", "target": "auth-1" },
                        { "role": "permission", "target": "applic-1",
                          "permission": "required", "source": "第五条" } ] }
  ]
}
```

- `reusable: true`（Authority / ContactDetails / ServiceHours / Applicability /
  NonStandardWorkingDay 默认如此）→ 不下发 `common`，即不填来源，便于跨文件复用
- `geometry` 只写 `geometries` 里的键名，几何图元由脚本生成
- 几何类型：`surface`（面，自动闭合环）/ `curve`（报告线、航路中心线）/ `point`
- 多文件来源：几何所依据的那份文件写进要素的 `sourceIndication`，
  其余来源写进 `textContent` 里的 `sourceIndication`

### VTS 类型要素的关联模式

```
VesselTrafficServiceArea (总区/子区)
  ├── controlAuthority ──→ Authority（原文指定的主管机构）
  ├── theContactDetails ──→ ContactDetails（原文适用的站点及联系方式）
  ├── reptForTrafficServ ──→ ShipReport（符合该类结构的独立报告类型）
  ├── consistsOf ──→ RadioCallingInPoint（报告线，自动补反向 componentOf）
  └── (可选) theRxN ──→ Regulations（有特殊规定时）

ShipReport
  ├── reportTo ──→ Authority
  └── mustBeFiledBy ──→ Applicability（如有本地定义的船舶类别）

RadioCallingInPoint
  └── componentOf ──→ VesselTrafficServiceArea（由脚本自动补齐）
```

逐个核实各子分区适用的主管机构、联系方式和报告安排。多个分区有相同安排时复用对应对象；
适用船舶、报告时机、报告内容、联系渠道或责任机构存在差异时分别编码。

## 参考资料（按需读，不要一次全读）

长文先按标题或要素类名定位，再用 `file_read` 的 `start_line` / `end_line` 分段读取；
若返回含 `(truncated)`，继续读取后续行，确保所需章节完整。

| 文件 | 什么时候读 |
|---|---|
| `references/01-通用编码规则.md` | 编 language / fixedDateRange / featureName / sourceIndication / textContent |
| `references/02-要素字典.md` | 选要素类、查必填项/几何约束/允许的关联/专有属性 |
| `references/03-枚举代码表.md` | 填任何枚举属性；51 个属性 / 452 个取值，全部来自 XSD |
| `references/04-关联关系.md` | 连关联、选许可/包含类型、判断能否省略 |
| `references/05-专题配方.md` | 按本次条文的用途判断候选要素，再到目录核对属性和关联 |
| `references/06-GML结构与几何.md` | 理解脚本输出、排查导入失败、核对几何；**XSD 状况、顺序之争、保真度实测、拓扑/平面编码差异说明** |
| `references/07-审核检查清单.md` | 交付前自检、判断某条文是否该表达、避开已知返工点与生产数据已知缺陷 |
| `references/08-字段抽取标注规范.md` | 各字段的抽取级别（必抽/条件抽/按需/慎用）、完整枚举代码表、特殊说明 |
| [references/09-UML结构与文本抽取规则.md](references/09-UML结构与文本抽取规则.md) | 原文抽取及输出结果前；查 UML 继承、基数、复合属性结构、JSON 输出映射及 §8.5 八列表格规则 |
| [references/10-地理要素含义与适用范围.md](references/10-地理要素含义与适用范围.md) | 选择地理要素类前；查候选类含义、适用与排除范围、相近类区别和判断条件 |
| [references/11-信息要素含义与适用范围.md](references/11-信息要素含义与适用范围.md) | 选择信息要素类前；查候选类含义、适用范围、继承字段和关联要求 |
| `schemas/S127.json` | 每个要素类的完整字段模板（给模型的输出格式参考） |

## 边界

- **不做** ENC/S-57 海图要素本身的编辑，也不改 `.000/.h2o` 文件
- **不做** 无线电、雷达等通信设施覆盖范围的表达（指南第 2 章明确不表达）
- 原文没给坐标时可引用已核实的 ENC 同名要素；缺少准确来源时请用户补充同名 GML。
  依据示意图勾绘的几何须注明依据并标为待确认。
