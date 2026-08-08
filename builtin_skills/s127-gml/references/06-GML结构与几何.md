# GML 结构与几何

理解 `build_gml.py` 的输出、排查导入失败、手工核对几何时读这一篇。
**正常流程中不需要手写任何 GML。**

---

## 零、手上这份 XSD 的状况（重要）

`schemas/S127.xsd`：targetNamespace `http://www.iho.int/S127/gml/cs0/1.0`，
version **1.0.0-20181129**，230 KB，42 个全局元素、72 个 complexType、53 个枚举类型。
命名空间与 13 个生产数据集完全一致。

**它给了什么**（都已抽成 `scripts/s127_catalogue.py`）：
要素类清单、每个要素类的子元素**顺序**与**多重性**（minOccurs/maxOccurs）、
复合类型的嵌套结构、几何类型约束、枚举 label 全集、哪些角色能挂在哪个要素类上。

**它没给什么，以及怎么补的**：

| 缺口 | 怎么解决 |
|---|---|
| 枚举的**数值 code**（XSD 只有 label） | 官方数值取自要素目录 S127FC.xml，每个取值自带 `code`；**不可按 1-based 序号推算**（431 个取值里 46 个不等）。实测 882 处 `code="N"` 与目录吻合 882/882，`gen_s127_catalogue.py --verify` 可随时复核 |
| `code` 属性本身没在 XSD 里声明 | XSD 把枚举元素定义成纯 `xs:string` 限定，没有 `xs:attribute name="code"`。但全部生产数据都带 `code`，系统也照收 —— 说明系统未按这份 XSD 做严格校验。构建器保持输出 `code`（与生产数据一致） |
| 伴随 schema 不在目录里 | `xs:include common.xsd`、`xs:import s100gmlbase.xsd / S100_gmlProfile.xsd / S100_gmlProfileLevels.xsd / s100gmlbaseExt.xsd` 都缺。缺的类型只有 `AbstractFeatureType`、`AbstractInformationType`（它们只贡献 `s100:featureObjectIdentifier`）和 6 个几何/数值基础类型，已在生成脚本里按 S-100 Part 10b 补齐 |
| 无法做真正的 `xmllint --schema` 校验 | 因为伴随 schema 缺失。替代方案是 `validate_featureset.py` —— 用 XSD 抽出的表在 Python 里做等价检查（存在性、必填、多重性、枚举、几何类型） |

**想做真 XSD 校验**：把 CARIS 的 `Product Information` 环境变量指向的整个目录
（含 `common.xsd` 与 `../../../S100/4.0.0/S100GML/20180502/` 下的文件）一并放进
`schemas/`，然后 `xmllint --noout --schema schemas/S127.xsd out.gml`。

---

## 零点五、顺序之争

XSD 的 `xs:sequence` 与生产数据的元素顺序在 **8/16 个要素类上不一致**，
形态完全统一：**XSD 把关联元素排在专有属性之前，CARIS 反过来**。

原因是继承链。S-127 的四层链（PS 图 7 / 指南 8.1）
`FeatureType → OrganisationContactArea → SupervisedArea → ReportableServiceArea`
把 `permission`、`theRxN`、`theContactDetails`、`controlAuthority`、`reptForTrafficServ`
定义在上层；XML Schema 规定 extension 的基类 sequence 必须排在派生 sequence 之前。
CARIS 导出时没有遵守这一点（可能用的是另一版 schema）。

复合类型也有两处：`fixedDateRange`（XSD `dateStart→dateEnd`，CARIS 相反）、
`sourceIndication`（`featureName` 的位置）。其余 18 个复合类型两边一致。

| | `--order xsd`（默认） | `--order caris` |
|---|---|---|
| 符合 S127.xsd sequence | **是** | 否 |
| 与 13 个已入库数据集版式一致 | 否 | **是**（实测逐元素一致） |
| 生产系统能否接受 | 极可能（见下） | 已验证 |

**为什么默认选 xsd**：生产系统接受了违反 XSD 顺序的 CARIS 数据，说明它没做严格顺序校验；
既然如此，符合标准的顺序同样会被接受，而且额外满足了规范。
**什么时候用 caris**：要和历史归档 GML 做逐行 diff，或续编同一个数据集。

> 如果导入时真的因顺序报错，切 `--order caris` 重出即可，要素清单不用改。

---

## 零点七、保真度实测（对 13 个生产数据集做往返）

```bash
python scripts/roundtrip_check.py '示例/**/*.gml'   # 内部开发用脚本，不对外暴露为工具
```

每个生产 GML → `gml_to_featureset.py` 反解 → `build_gml.py` 重建 →
与原件逐要素比对。当前结果：

| 项目 | 结果 |
|---|---|
| 完成往返 | 13/13 个数据集 |
| **几何** | **161/161 个要素坐标序列完全一致**（点数、7 位小数全同） |
| 元素结构 | 非预期差异 **0** 处 |
| 预期内差异 | 44 处（见下） |

**两类预期内差异**：

1. **43 处 `displayName`**：CARIS 显式写 `<displayName>false</displayName>`，
   本构建器按指南「不选」直接省略。XSD 里 `displayName` 是 `minOccurs=0`，
   省略即默认不显示 —— **语义等价**。（全语料分布：eng 335 缺省 / 75 false / 32 true；
   zho 187 缺省 / 58 false / 231 true —— 生产数据本身就不统一。）
2. **1 处 `s100:maskReference`**：`<s100:surfaceProperty><s100:maskReference
   xlink:href="#c72" xlink:role="suppressed"/></s100:surfaceProperty>`，
   用于抑制部分边界的显示。**本构建器不支持**，需要时只能在 CARIS 里补。

**几何分段不算差异**：生产文件一个环常由很多 `Curve` 段拼成
（宁德 VTS：1018 个 Curve 拼 54 个 CompositeCurve，其中一个环含 628 段），
本构建器一个环出 1 段。解析后的坐标序列完全相同，图元数量不同而已。

---

## 零点八、posList 坐标数差异（拓扑编码 vs 平面编码）

CARIS 产出的 GML 使用**拓扑几何编码**，本构建器使用**平面几何编码**。
两种编码的空间几何完全等价（形状/坐标一致），仅 GML 文件中 `posList` 的数量
和总坐标对数有差异。**这不是错误，不影响系统导入和数据正确性。**

### 两种编码模式对比

| | CARIS 拓扑编码 | 本构建器平面编码 |
|---|---|---|
| 每个环的 Curve 数 | N 段（在拓扑节点处拆分） | **1 段** |
| CompositeCurve 的 curveMember | N 个引用 | **1 个引用** |
| OrientableCurve 共享 | 同一 OC 可被多个 CC 引用（共享边界） | 每环独享，不共享 |
| posList 总数 | = Curve 段数（较多） | = 环/线数（较少） |

### 差异产生的两个机制

**机制 A：Junction 点去重（重建后坐标变少）**

CARIS 把一个环拆成 N 段 Curve，相邻两段在连接点（junction）处各存一份坐标。
`gml_to_featureset.py` 拼接时去除重复的连接点（`pts[1:]`），结果每个环减少 (N-1) 个点。

实测验证：

| 案例 | 原始段数→环数 | 理论去除 junction | 实际差(原-建) |
|---|---|---|---|
| 宁德 VTS | 1020→54 | 966 | **962** ✓ |
| 厦门桥区 | 34→15 | 19 | **19** ✓ |
| 珠江口定线制 | 86→26 | 60 | **38** |
| 渔船航路 | 39→7 | 32 | **22** |

宁德和厦门精确吻合。珠江口/渔船航路差值偏小，原因是 `build_gml.py` 的
`close_ring()` 为未闭合的环补回了首尾点。

**机制 B：OC 共享边界展开（重建后坐标变多）**

当多个多边形共享边界时，CARIS 让同一 OrientableCurve 段被多个 CompositeCurve
引用——坐标只存一份。`gml_to_featureset.py` 为每个环独立拼接，共享段的坐标被
复制到每个引用方，导致重建后坐标总量增加。

实测验证：

| 案例 | 共享 OC 数 | 共享段坐标对 | 理论额外复制 | 实际差(建-原) |
|---|---|---|---|---|
| 天津交管 | 15 | 4,664 | 6,188 | **+6,084** ✓ |
| 宁德 VTS | 2 | 4 | 4 | (被机制A抵消) |

天津案例误差 ~100 对（6188-6084），由 junction 去重抵消。

### 何时无差异

当原始 GML 本身就是平面编码（每环 1 段、无共享）时，posList 数和坐标总数完全一致：

- 商渔船碰撞风险区（天津）：8 posList / 395 坐标对 — 完全匹配
- 沿海公共航路（丹东大连）：3 posList / 17 坐标对 — 完全匹配
- 避险区域（平潭）：6 posList / 51 坐标对 — 完全匹配

### 结论

- **不需要实现拓扑构建器**：平面编码是合法的 S-100 GML，导入系统接受
- **对比工具应忽略 posList 数量差异**：只对比几何形状（坐标序列去重后比较）
- **有拓扑复杂度的案例**：VTS 类数据集最常见，因为 VTS 子分区天然共享边界

---

## 一、数据集骨架

```xml
<?xml version="1.0" encoding="UTF-8"?>
<S127:Dataset xmlns:xsi="…" xmlns:xlink="http://www.w3.org/1999/xlink"
              xmlns:gml="http://www.opengis.net/gml/3.2"
              xmlns:s100="http://www.iho.int/s100gml/1.0"
              xmlns:S127="http://www.iho.int/S127/gml/cs0/1.0"
              xmlns="http://www.iho.int/S127/gml/cs0/1.0" gml:id="ds">
  <gml:boundedBy>…</gml:boundedBy>                    <!-- 覆盖全部几何的外包框 -->
  <s100:DatasetIdentificationInformation>…</s100:DatasetIdentificationInformation>
  <s100:Curve …/> <s100:OrientableCurve …/>           <!-- 几何图元，先于 members -->
  <s100:CompositeCurve …/> <s100:Surface …/>
  <members>
    <Authority gml:id="i1">…</Authority>              <!-- 信息要素 i1、i2… -->
    <PlaceOfRefuge gml:id="f1">…</PlaceOfRefuge>      <!-- 地理要素 f1、f2… -->
  </members>
</S127:Dataset>
```

### DatasetIdentificationInformation 固定值

| 元素 | 值 |
|---|---|
`encodingSpecification` | `S-100 Part 10b`
`encodingSpecificationEdition` | `1.0`
`productIdentifier` | `INT.IHO.S-127.1.0`
`productEdition` | `1.0.0`
`applicationProfile` | `1`
`datasetFileIdentifier` | `<数据集名>.gml`
`datasetTitle` | `<数据集名>`
`datasetReferenceDate` | 数据集制作日期 `YYYY-MM-DD`
`datasetLanguage` | `EN`
`datasetTopicCategory` | `transportation`
`datasetPurpose` | `base`
`updateNumber` | `0`（基础数据集）

### 坐标参考系

`srsName="http://www.opengis.net/def/crs/EPSG/0/4326"`，
**轴序为「纬度 经度」**。`posList` 里写 `25.5915 119.6846667` —— 纬度在前。

---

## 二、几何图元链

S-127 不直接用 GML 的 Polygon/LineString，而是走 S-100 的图元链：

```
面： Curve ──► OrientableCurve ──► CompositeCurve ──► Surface(PolygonPatch/Ring)
线： Curve ──► OrientableCurve ──► CompositeCurve
点： Point
```

```xml
<s100:Curve srsName="…" gml:id="c1">
  <gml:segments><gml:LineStringSegment>
    <gml:posList>25.5915 119.6846667 25.5905 119.6896667 … 25.5915 119.6846667</gml:posList>
  </gml:LineStringSegment></gml:segments>
</s100:Curve>
<s100:OrientableCurve srsName="…" gml:id="oc1" orientation="+">
  <gml:baseCurve xlink:href="#c1"/>
</s100:OrientableCurve>
<s100:CompositeCurve srsName="…" gml:id="cc1">
  <gml:curveMember xlink:href="#oc1"/>
</s100:CompositeCurve>
<s100:Surface srsName="…" gml:id="s1">
  <gml:patches><gml:PolygonPatch>
    <gml:exterior><gml:Ring><gml:curveMember xlink:href="#cc1"/></gml:Ring></gml:exterior>
    <!-- 有孔洞时追加 <gml:interior>…</gml:interior> -->
  </gml:PolygonPatch></gml:patches>
</s100:Surface>
```

要素通过 `<geometry>` 引用：

```xml
<geometry><s100:surfaceProperty xlink:href="#s1"/></geometry>   <!-- 面 -->
<geometry><s100:curveProperty  xlink:href="#cc1"/></geometry>   <!-- 线，指向 CompositeCurve -->
<geometry><s100:pointProperty  xlink:href="#pt1"/></geometry>   <!-- 点 -->
```

### orientation 属性

CARIS 导出时会因内部拓扑写出 `+` 或 `-`（语料中两者都大量出现）。
`build_gml.py` 统一输出 `+` 并按给定坐标顺序建环 —— 几何等价，导入无差别。
若要与既有 CARIS 工程逐字节比对，`orientation` 的差异可忽略。

### 环的闭合

面要素外环首尾点必须相同。`build_gml.py` 自动闭合，`coords.py --close` 也会闭合。

---

## 三、gml:id 分配

| 前缀 | 用途 |
|---|---|
`f1`, `f2`… | 地理要素（按 features 数组顺序） |
`i1`, `i2`… | 信息要素 |
`c`, `oc`, `cc`, `s`, `pt` | 几何图元 |

`s100:featureObjectIdentifier` 只有地理要素才有：

```xml
<s100:featureObjectIdentifier>
  <s100:agency>CN</s100:agency>
  <s100:featureIdentificationNumber>868</s100:featureIdentificationNumber>
  <s100:featureIdentificationSubdivision>1</s100:featureIdentificationSubdivision>
</s100:featureObjectIdentifier>
```

FIN 由 `dataset.featureIdStart` 起递增。**同一发布体系内 FIN 不应重复** ——
新数据集的起始值要向数据管理方确认，不要随意从 1 开始。

---

## 四、双语文本的编码

「先英文后中文」在 GML 里是**一个元素内用 CR 分隔**（不是两个元素）：

```xml
<source>Fujian Navigational Notice No.0438/2025&#13;
闽航通〔2025〕0438号</source>
```

`&#13;` 是回车（CR）的实体转义。**必须转义** —— 直接写裸 CR 会在 XML 解析时被
规范化成 LF，双语分隔就丢了。`build_gml.py` 已处理。

而 `featureName` 与 `information` 是**两个独立元素**，各带 `language`：

```xml
<featureName><language>eng</language><name>PINGTAN MSA</name></featureName>
<featureName><displayName>true</displayName><language>zho</language><name>平潭海事局</name></featureName>
```

---

## 五、枚举属性的写法

```xml
<sourceType code="1">law or regulation</sourceType>
<restriction code="27">speed restricted</restriction>
```

`code` 与文本必须**成对匹配**。要素清单里只写 label，`build_gml.py` 从
`scripts/s127_catalogue.py`（XSD 生成）查表补 `code` —— 手工改 GML 时最容易出的错
就是改了文本没改 code。全量对照表见 `03-枚举代码表.md`。

注意 `code` 属性本身并未在这份 XSD 里声明（见 §零），但全部生产数据都带它，
构建器因此保留输出。

---

## 六、关联元素

```xml
<controlAuthority xlink:href="#i1"
                  xlink:arcrole="http://www.iho.net/S-127/roles/controlAuthority"
                  xlink:title="SrvControl"/>
```

空元素（XSD 类型 `gml:ReferenceType`），三个属性缺一不可。

关联类型（required / included 等）默认**不在** GML 里，进补录清单；
但 schema 其实支持用 `PermissionType` / `InclusionType` 对象承载
（`--assoc-objects`）—— 见 `04-关联关系.md` §五。

---

## 七、CARIS S-57 Composer 工作流（指南 3.3.1）

本技能生成的 GML 可直接导入值班子系统；若走 CARIS 路线：

1. `Tools — Options — Files and Folders`，确认 `Catalogue Control`、`Product Information`
   两个环境变量指向 S-127 的配置文件
2. 打开参考数据源（`.000/.h2o` ENC、`.shp`、`.dwg`、`.img/.tif/.csar`）
3. `File — New — Product — S-100`，填数据集管理与元数据信息、地理范围
4. 导入数据到产品层或临时层并编辑
5. 编辑中保存工程文件；完成后导出为 GML，在 output 窗口确认输出路径

**Composer 的两个已知限制**（指南 3.3.2(4)）：
- 无法选择关联关系类型
- `ContactDetails` 缺少 `radiocommunications` 属性（XSD 里 c 小写）

两者都需在值班子系统内补充编辑。

---

## 八、导入与存档（指南 3.3.2）

1. 地图窗口右上「导入GML」→ 选文件 → 确认导入成功、**导入数量准确**
2. 核对要素属性与关联关系的完整性、准确性
3. 「数据明细」列出所有 S-127 要素（按导入先后排列），可筛选后批量操作
4. 「关联要素」里补选关联关系类型
5. 一审/二审意见改完后，**GML 必须与系统同步**：
   属性修改、要素增减、要素类型转变、要素范围变化（**关联类型变动除外**）
6. 最终 GML 归档到共享文件夹

---

## 九、排查

| 现象 | 检查 |
|---|---|
导入数量少于预期 | `members` 内要素数；`gml:id` 是否重复 |
要素无几何/位置错乱 | `geometry` 的 `xlink:href` 是否指向存在的图元；面指向 Surface、线指向 CompositeCurve |
位置偏到内陆/海外 | 经纬度写反（`posList` 是「纬度 经度」）；跑 `validate_featureset.py` 会告警 |
关联丢失 | 目标 `gml:id` 是否存在；`arcrole` 是否正确 |
中文乱码 | 文件必须 UTF-8 无 BOM |
双语只显示英文 | `&#13;` 是否被替换成了 LF |
枚举显示异常 | `code` 与文本是否匹配；code 是否在要素目录中存在 |
