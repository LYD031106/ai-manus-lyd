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

## 核心原则：不要手写 GML

GML 的命名空间、几何图元链、`gml:id` 串联、`boundedBy`、`xlink:arcrole`
一处写错整份数据集就废了。**本技能的分工是：你负责判断与编码，脚本负责序列化。**

```
多个 PDF/Word → ①解析为 MD → ②基于 MD 生成要素清单 JSON → ③校验 → ④构建 GML → ⑤记录表 → ⑥系统导入与补录
 （用户上传）  （parse_office+parse_regulation）（你的产出）      （脚本）   （脚本）    （脚本）    （人工）
```

绝不直接生成 `.gml` 文本。只生成 `featureset.json`，然后用脚本完成后续步骤。

### ① 解析：按文件类型分两条路

| 文件类型 | 怎么处理 | 为什么 |
|---|---|---|
| Word / Excel / CSV | 沙箱内跑 `parse_office.py` | 确定性文本提取，快且不花 token |
| PDF / 图片 | `parse_regulation` 工具 | 需要 OCR、表格识别、读图上的标注 |
| Word 里的内嵌图片 | `parse_office.py` 导出 → 再交 `parse_regulation` | 示意图上的坐标只有模型读得出来 |

```bash
S=/opt/skills/s127-gml/scripts

# Word / Excel / CSV 本地解析；Word 内嵌图片会一并导出，输出里会列出图片路径
python3 $S/parse_office.py /home/ubuntu/upload/附件1.docx /home/ubuntu/upload/坐标.csv \
        -o /home/ubuntu/解析-office.md
```

```
# PDF、图片，以及上一步导出的示意图
parse_regulation(files=["/home/ubuntu/upload/通告.pdf",
                        "/home/ubuntu/附件2示意图_img1.png"],
                 output="/home/ubuntu/解析-pdf.md")

# 只提坐标（跳过全文解析）
parse_regulation(files=["/home/ubuntu/upload/坐标附件.pdf"], coords_only=true,
                 output="/home/ubuntu/coords.json")
```

`parse_regulation` 参数：
- `files`（string[]，必填）：绝对路径，**只接受** .pdf/.png/.jpg/.gif/.webp
- `output`（string，必填）：输出文件路径，绝对路径
- `coords_only`（bool，可选）：仅提取坐标，输出 JSON
- `model`（string，可选）：覆盖默认模型

两条路的产出都要读，合起来才是完整素材。`.doc` / `.wps` 两边都不支持，先转 `.docx`。

### ②~⑤ 后续步骤：调用本地脚本

```bash
S=/opt/skills/s127-gml/scripts

# ② 你阅读 解析结果.md，生成 featureset.json

# ③ 校验，改到 0 ERROR
python3 $S/validate_featureset.py featureset.json

# ④ 构建 GML
python3 $S/build_gml.py featureset.json \
       -o 127CN00XXX001.gml --assoc-csv 关联补录清单.csv

# ⑤ 出记录表
python3 $S/make_record_table.py featureset.json \
       -o S-127要素生产与检查记录表.xlsx
```

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

## 七步工作流

### ① 解析源文件与识别专题
用户上传的通常是**多个文件**：通告正文 PDF、附件 Word（.docx）、坐标表 CSV/Excel、
示意图图片等。按上面 §① 的两条路分别解析：

```bash
# 第一步：Office 文档本地解析，顺带导出 Word 里的示意图
python3 /opt/skills/s127-gml/scripts/parse_office.py \
        /home/ubuntu/upload/附件1.docx /home/ubuntu/upload/坐标.csv \
        -o /home/ubuntu/解析-office.md
```

读一遍 `解析-office.md`，若里面列出了导出的图片路径，把它们和 PDF 一起交给
`parse_regulation`（第二步）。

- Word (.docx)：本地提文本与表格；**跨列合并的表会带告警**，涉及坐标务必对照原文核对
- CSV / Excel：本地解析为 Markdown 表格
- PDF / 图片 / Word 导出的示意图：`parse_regulation`（OCR、图中坐标、表格识别）
- .doc / .wps：不支持，先用 shell 转成 .docx 再解析

然后阅读解析结果 Markdown，确定：覆盖的地理范围、发文机构层级、属于哪个专题。
专题决定要素配方 —— 见 `references/05-专题配方.md`，里面按七类专题给出了
「该建哪些要素、关联怎么连」的成品清单，照抄即可，不要从零设计。

同时确认文件**有效性**：按发布时间从新到旧采用；新文件完全取代旧文件时旧的不采用；
必要时核对《现行有效规范性文件目录》。

#### 多文件合并原则（核心规则）

> **无论源文件有几个，最终只生成一个 GML 数据集。**

多个源文件描述同一个管理体系（如：VTS 管理细则 + VTS 用户指南 + 报告制）时的处理：

1. **内容融合不拆分**：所有文件的内容合并到同一个 featureset.json 中，生成一个 GML
2. **出处追溯到条款**：每个要素的 `clause` 字段记录其来源——格式为 `《文件简称》第X条`
   或 `VTS指南 X.X`。多个来源用分号隔开
3. **几何取定位文件**：要素的 `sourceIndication` 填描述其地理位置/坐标的那份文件；
   其他文件的条文内容填进 `textContent`
4. **新文件优先**：内容冲突时以发布时间最新的文件为准
5. **互补不重复**：不同文件对同一要素的描述应合并（如 A 文件给坐标、B 文件给规则），
   而不是建两个重复要素

### ② 抽取共同属性
文件头/尾一般给出全数据集共用的两组属性，放进 JSON 的 `common`：

- `fixedDateRange`：生效/失效时间（"自2024年9月7日起施行，有效期五年" → `2024-09-07` / `2029-09-06`）
- `sourceIndication`：发文机构、文号、发文日期、发文类型

细则见 `references/01-通用编码规则.md` §2、§4。

### ③ 逐条转换为要素
对每一条条文问三个问题，顺序不能颠倒：

1. **要不要表达？** 常识性内容、海上作业申报、入港申请、指挥中心临时管制条件
   一律不转换 —— 完整清单见 `references/07-审核检查清单.md` §不表达清单。
2. **ENC 是否已表达？** 已表达的等同内容不转换为 S-127（指南 3.2(2)）。
   坐标与 ENC 一致的报告线/引航登离轮点，直接从 ENC 导入而不是重画。
3. **该建地理要素还是信息要素？**
   - 条文只作用于**单一地理要素** → 直接填该地理要素的 `textContent`，**不建信息要素**
   - 条文作用于**多个**地理要素，或需要**特定条件**（能见度/气象/水文）才成立
     → 建 Regulations / Restrictions 信息要素再关联
   - 直属海事局及以上发文 → `Regulations`；地方海事局及同级 → `Restrictions`

要素类的选型判断表在 `references/02-要素字典.md`。几个最容易错的：
| 条文特征 | 正确要素 |
|---|---|
| 「禁止会遇」 | `WaterwayArea`（**不是**限制区） |
| 单向/双向通航等交通管制 | `RestrictedAreaNavigational` |
| 商渔船碰撞高风险警示区 | `ConcentrationOfShippingHazardArea` |
| 桥区水域 + 净空高 | 桥区范围建要素，净空信息填 `textContent` |
| 富余水深，不同条件 | 每个条件各建一个 `UCAA`，即使几何完全相同 |
| 船舶避险推荐水域 | `PlaceOfRefuge` |

### ④ 编码属性
- 一律**先英文后中文**；复合属性里的 `language` 用 ISO 639-3（`eng` / `zho`）
- 单值文本属性写成 `["English text", "中文文本"]`，脚本会用 CR 连成一格
- `featureName` 写成 `{"eng": "...", "zho": "..."}`，脚本按规则补 `displayName`
- 枚举属性写 label（如 `"speed restricted"`），脚本从 XSD 查表补 `code`
- 命名尽量精简，体现所属区域/系统与功能；英文词首大写，数值加千分位
- **只填原文有的**：XSD 必填项见 `references/02-要素字典.md` §二那张表，
  其余属性一律「有则填、无则不填」，不要为了"填满"而编造内容

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
依次运行：`validate_featureset.py` → `build_gml.py` → `make_record_table.py`。
`validate_featureset.py` 的 ERROR 必须清零；WARN 逐条看过再决定忽略。
交付前对照 `references/07-审核检查清单.md` 走一遍。

#### 高频返工问题（来自真实审核记录）

以下是从已入库数据集的一审/二审意见中统计出的高频返工点，生成 JSON 前必须自查：

| 返工问题 | 正确做法 |
|---|---|
| textContent 与 ShipReport 内容重复 | 报告格式、报告内容、报告时机等**只通过 ShipReport 表达**，不在地理要素的 textContent 里重复 |
| Authority 重复创建 | 同一机构（如「福建海事局」「天津海事局」）全数据集**只建一个**，所有要素共用关联 |
| ContactDetails 缺 language 属性 | 中文联系方式和英文联系方式**各建一条**，必须填 `"language": "zho"` 或 `"eng"` |
| 英文名称未首字母大写 | 每个实词首字母大写：`Ningde VTS Report Line`，非 `ningde vts report line` |
| VHF 内容写在 textContent 里 | VHF 频道填对应要素的 `communicationChannel` 属性或 `requirementsForMaintenanceOfListeningWatch`；详细通信信息填 ContactDetails 的 `telecommunications` |
| Applicability 合并为一条 | 每个船舶类别**各建一条**独立的 Applicability（如「客船」「危险品船舶」「300总吨及以上中国籍船舶」各一条） |
| 关联了不存在的系统实体 | 只关联本数据集内定义的要素。需要关联系统已有实体的（如 ServiceHours「24小时值班」），在记录表备注栏标「系统内关联」 |
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
| 桥区水域 | `BA` | `127CN00XMBA002` |
| 避险推荐水域 | `WRVEW` | `127CN00PTWRVEW001` |

## 要素清单 JSON 结构

完整可跑的样例：`templates/featureset.example.json`
（复现真实数据集 `127CN00PTWRVEW001`；`--order caris` 构建结果与之逐元素一致）。
空白骨架：`templates/featureset.skeleton.json`（VTS 专题，含 10 个要素与全套关联）。
字段约定：`schemas/featureset.schema.json`。S-127 权威 schema：`schemas/S127.xsd`。

```jsonc
{
  "dataset":  { "name": "127CN00PTWRVEW001", "referenceDate": "2025-12-26",
                "updateNumber": 0, "agency": "CN", "featureIdStart": 868 },
  "common":   { "fixedDateRange": {...}, "sourceIndication": {...} },  // 自动下发给所有专属要素
  "geometries": { "g-1": { "type": "surface", "exterior": [[25.5915, 119.6846667], ...] } },
  "features": [
    { "ref": "auth-1", "type": "Authority", "reusable": true,
      "featureName": { "eng": "PINGTAN MSA", "zho": "平潭海事局" },
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

### VTS 类型要素的典型关联模式（真实数据集提炼）

```
VesselTrafficServiceArea (总区/子区)
  ├── controlAuthority ──→ Authority（主管机构，1个）
  ├── theContactDetails ──→ ContactDetails（中英各1份 × N 站点）
  ├── reptForTrafficServ ──→ ShipReport（每种报告类型1条）
  ├── consistsOf ──→ RadioCallingInPoint（报告线，自动补反向 componentOf）
  └── (可选) theRxN ──→ Regulations（有特殊规定时）

ShipReport
  ├── reportTo ──→ Authority
  └── mustBeFiledBy ──→ Applicability（如有本地定义的船舶类别）

RadioCallingInPoint
  └── componentOf ──→ VesselTrafficServiceArea（由脚本自动补齐）
```

> **关键原则**：多个 VTS 子分区**共享**同一套 ShipReport / ContactDetails / Authority，
> 不要为每个分区重复创建。每种报告类型（sailing plan / final / deviation / other）只建一条。

## 参考资料（按需读，不要一次全读）

| 文件 | 什么时候读 |
|---|---|
| `references/01-通用编码规则.md` | 编 language / fixedDateRange / featureName / sourceIndication / textContent |
| `references/02-要素字典.md` | 选要素类、查必填项/几何约束/允许的关联/专有属性 |
| `references/03-枚举代码表.md` | 填任何枚举属性；51 个属性 / 452 个取值，全部来自 XSD |
| `references/04-关联关系.md` | 连关联、选许可/包含类型、判断能否省略 |
| `references/05-专题配方.md` | 开工第一件事：按专题照抄要素配方 |
| `references/06-GML结构与几何.md` | 理解脚本输出、排查导入失败、核对几何；**XSD 状况、顺序之争、保真度实测、拓扑/平面编码差异说明** |
| `references/07-审核检查清单.md` | 交付前自检、判断某条文是否该表达、避开已知返工点与生产数据已知缺陷 |
| `references/08-字段抽取标注规范.md` | 各字段的抽取级别（必抽/条件抽/按需/慎用）、完整枚举代码表、特殊说明 |
| `schemas/S127.json` | 每个要素类的完整字段模板（给模型的输出格式参考） |

## 边界

- **不做** ENC/S-57 海图要素本身的编辑，也不改 `.000/.h2o` 文件
- **不做** 无线电、雷达等通信设施覆盖范围的表达（指南第 2 章明确不表达）
- 几何范围原文没给坐标时：优先取 ENC 同名要素，其次按示意图勾绘，
  并在记录表里写明来源依据 —— 勾绘结果必须请人复核，不要当成确定数据交付
