# S-127 文本抽取与编码规则

本规则供 Agent 从海事法规、通告、服务指南及附件中提取 S-127 要素，形成可校验的结构化数据，再交由程序生成 GML。结构依据为用户提供的三张 UML 图，分别覆盖地理要素、通用基类及复合属性、信息要素。

本文将图示结构和抽取操作约定分别标明。图示未给出完整枚举、全部关联、部分复合类型内部结构和 GML 序列化细节，执行时须加载项目选定版本的要素目录、XSD 与编码约定。遇到版本差异，记录差异并按任务明确采用的版本处理。

本技能中，本文负责图示结构与抽取流程，[08-字段抽取标注规范.md](08-字段抽取标注规范.md) 负责字段级抽取细则，[05-专题配方.md](05-专题配方.md) 负责专题选择。JSON 文件遵循 [featureset.schema.json](../schemas/featureset.schema.json) 和第 8 节的项目映射。图中数量与当前目录或实现不一致时，记录差异并核对采用版本，不直接改写机器目录。

选择具体类前，先查 [10-地理要素含义与适用范围.md](10-地理要素含义与适用范围.md) 或 [11-信息要素含义与适用范围.md](11-信息要素含义与适用范围.md) 的使用依据及候选类章节，核对含义、适用和排除范围。类型判断及其来源写入独立的 `evidence.json`，字段标记为 `type`。

## 1. Agent 的执行规则

1. 先定位原文中的对象、行为、适用船舶、空间范围、时间、机构和通信信息，再选择具体要素类。
2. 按具体类的完整继承链收集可用属性，再检查本类字段及数量约束。抽象类只提供继承结构。
3. 每个提取结果必须能够追溯到文件、页码或条款及原文。字段推断、翻译、来源缺失和待确认情况分别标记。
4. 字段名称及大小写必须与采用的目录一致。类名如 `RestrictedAreaNavigational`，属性名如 `sourceIndication`。
5. 枚举值必须查目录中的标签、代码和本类允许子集。图中出现的类型名不等于枚举取值。
6. 可选字段无依据时省略。必填字段无依据时记录待补项，并使用目标规范明确支持的未知值表示方法。禁止用空字符串、`false`、`0`、当前日期或编造内容补齐。
7. 结构化中间数据先通过类型、基数、枚举、引用与几何检查，再生成 GML。
8. 只有实际执行了对应检查，才可在报告中声明通过该检查。来源语义核对、几何核对、完整 XSD 校验和软件导入分别记录结果。

## 2. UML 数量与数据类型

| 图中标记 | 含义 | 抽取与校验要求 |
| --- | --- | --- |
| `[0..1]` | 可选，最多一个 | 无依据时省略 |
| `[1..1]` 或省略数量标记 | 必填，恰好一个 | 缺失时登记待补项 |
| `[0..*]` | 可选，可重复 | 每个有依据的值独立记录 |
| `[1..*]` | 至少一个，可重复 | 不能以空数组交付完整成果 |
| `[0..2]` | 可选，最多两个 | 超过两个需重新检查原文及建模 |
| `{ordered}` | 值具有顺序 | 保持规定顺序 |
| 斜体类名 | 抽象类 | 通过具体子类实例化 |
| 空心三角箭头 | 继承 | 箭头所指为父类 |
| 空心菱形 | 聚合 | 以对象引用表达整体与组成部分 |

| 类型 | 操作约定 |
| --- | --- |
| `text` | 字符串，保留事实内容及必要单位 |
| `Boolean` | 只有原文能明确判断时填真或假；未提及应按缺失规则处理 |
| `Real` | 数值，单位与取值范围另查属性定义 |
| `Integer` | 整数，不能自行假定单位 |
| `URL` | 从原文提取可识别的地址 |
| `ISO639-3` | 三字母语言代码，如 `zho`、`eng` |
| `S100_TruncatedDate` | 按采用版本支持的日期精度和词法编码；原文缺月日时不得自行补齐 |
| `PointOrCurve`、`PointOrSurface`、`CurveOrSurface` | 分别允许点或线、点或面、线或面 |
| `GM_Surface`、`GM_OrientableSurface` | 按采用版本的几何模型和 GML 表达处理 |
| 其他命名类型 | 查要素目录定义，尤其是枚举和复合属性 |

图中的数量约束适用于 UML 模型层。数组、单对象及项目中的双语简写如何映射到 JSON，应统一遵循中间数据 Schema。多个列表项始终保留各自边界。

## 3. 基类与继承

### 3.1 通用基类

| 基类 | 属性 | 类型 | 数量 |
| --- | --- | --- | --- |
| `FeatureType` | `fixedDateRange` | `fixedDateRange` | `0..1` |
| `FeatureType` | `periodicDateRange` | `periodicDateRange` | `0..*` |
| `FeatureType` | `featureName` | `featureName` | `0..*` |
| `FeatureType` | `sourceIndication` | `sourceIndication` | `0..1` |
| `FeatureType` | `textContent` | `textContent` | `0..*` |
| `InformationType` | `fixedDateRange` | `fixedDateRange` | `0..1` |
| `InformationType` | `periodicDateRange` | `periodicDateRange` | `0..*` |
| `InformationType` | `featureName` | `featureName` | `0..*` |
| `InformationType` | `sourceIndication` | `sourceIndication` | `0..*` |

`FeatureType.sourceIndication` 最多一个，`InformationType.sourceIndication` 可重复。`InformationType` 基类没有声明 `textContent`，允许该属性的信息子类在后文单独列出。

### 3.2 地理要素继承树

```text
FeatureType（抽象）
├── RadioCallingInPoint
├── SignalStationWarning
├── RadarRange
├── ConcentrationOfShippingHazardArea
├── PilotageDistrict
├── RouteingMeasure
├── UnderkeelClearanceAllowanceArea
├── CautionArea
└── OrganisationContactArea（抽象）
    ├── SignalStationTraffic
    ├── IspsCodeSecurityLevel
    ├── PilotBoardingPlace
    └── SupervisedArea（抽象）
        ├── MilitaryPracticeArea
        ├── RestrictedAreaRegulatory
        ├── RestrictedAreaNavigational
        ├── WaterwayArea
        └── ReportableServiceArea（抽象）
            ├── VesselTrafficServiceArea
            ├── ShipReportingServiceArea
            ├── LocalPortServiceArea
            ├── PiracyRiskArea
            ├── UnderkeelClearanceManagementArea
            ├── PlaceOfRefuge
            └── PilotService
```

图中 `OrganisationContactArea`、`SupervisedArea`、`ReportableServiceArea` 未展开新增字段。具体关联及可用角色应查目录，不能仅由类名推断。

### 3.3 信息要素继承树

```text
InformationType（抽象）
├── Authority
├── ShipReport
├── NonStandardWorkingDay
├── ServiceHours
├── Applicability
├── ContactDetails
└── AbstractRxN（抽象）
    ├── NauticalInformation
    ├── Regulations
    ├── Restrictions
    └── Recommendations
```

## 4. 地理要素的本类字段

下面仅列本类新增字段。每一类均需继承第 3 节中的通用字段。`字段: 类型 [数量]` 表示一项属性。

| 具体类 | 本类非几何字段 | 几何类型及数量 |
| --- | --- | --- |
| `RadioCallingInPoint` | `callSign: text [0..1]`；`categoryOfCargo: categoryOfCargo [0..*]`；`categoryOfVessel: categoryOfVessel [0..*]`；`communicationChannel: text [0..*]`；`status: status [0..*]`；`trafficFlow: trafficFlow [1..1]`；`orientationValue: Real [0..2]` | `PointOrCurve [0..*]` |
| `SignalStationWarning` | `categoryOfSignalStationWarning: categoryOfSignalStationWarning [1..*]`；`communicationChannel: text [0..*]`；`status: status [0..*]` | `PointOrSurface [0..*]` |
| `RadarRange` | `communicationChannel: text [0..*]`；`status: status [0..*]` | `GM_Surface [0..*]` |
| `ConcentrationOfShippingHazardArea` | `categoryOfConcentrationOfShippingHazardArea: categoryOfConcentrationOfShippingHazardArea [0..*]`；`status: status [0..*]` | `GM_OrientableSurface [1..*]` |
| `PilotageDistrict` | `communicationChannel: text [0..*]` | `GM_Surface [0..*]` |
| `RouteingMeasure` | `categoryOfRouteingMeasure: categoryOfRouteingMeasure [1..1]`；`categoryOfTrafficSeparationScheme: categoryOfTrafficSeparationScheme [0..1]`；`categoryOfNavigationLine: categoryOfNavigationLine [0..1]` | `CurveOrSurface [1..*]` |
| `UnderkeelClearanceAllowanceArea` | `underkeelAllowance: underkeelAllowance [0..1]`；`waterLevelTrend: waterLevelTrend [0..1]` | `GM_OrientableSurface [1..*]` |
| `CautionArea` | `condition: condition [0..1]`；`status: status [0..1]` | `PointOrSurface [0..*]` |
| `SignalStationTraffic` | `categoryOfSignalStationTraffic: categoryOfSignalStationTraffic [1..*]`；`communicationChannel: text [0..*]`；`status: status [0..*]` | `PointOrSurface [0..*]` |
| `IspsCodeSecurityLevel` | `ispsLevel: ispsLevel [1..1]` | `CurveOrSurface [1..*]` |
| `PilotBoardingPlace` | `callSign: text [0..1]`；`categoryOfPilotBoardingPlace: categoryOfPilotBoardingPlace [0..1]`；`categoryOfPreference: categoryOfPreference [0..1]`；`categoryOfVessel: categoryOfVessel [0..1]`；`communicationChannel: text [0..*]`；`destination: text [0..1]`；`pilotMovement: pilotMovement [0..1]`；`pilotVessel: text [0..1]`；`status: status [0..*]` | `PointOrSurface [0..*]` |
| `MilitaryPracticeArea` | `categoryOfMilitaryPracticeArea: categoryOfMilitaryPracticeArea [0..*]`；`nationality: text [0..1]`；`restriction: restriction [0..*]`；`status: status [0..*]` | `PointOrSurface [1..*]` |
| `RestrictedAreaRegulatory` | `categoryOfRestrictedArea: categoryOfRestrictedArea [0..*]`；`restriction: restriction [0..*]`；`status: status [0..*]` | `GM_OrientableSurface [1..*]` |
| `RestrictedAreaNavigational` | `categoryOfRestrictedArea: categoryOfRestrictedArea [0..*]`；`restriction: restriction [1..*]`；`status: status [0..*]` | `GM_OrientableSurface [1..*]` |
| `WaterwayArea` | `siltationRate: text [0..1]`；`status: status [0..*]`；`dynamicResource: dynamicResource [1..1]` | `GM_OrientableSurface [1..*]` |
| `VesselTrafficServiceArea` | `categoryOfVesselTrafficService: categoryOfVesselTrafficService [0..*]`；`serviceAccessProcedure: text [0..1]`；`requirementsForMaintenanceOfListeningWatch: text [1..1]` | `GM_OrientableSurface [0..*]` |
| `ShipReportingServiceArea` | `serviceAccessProcedure: text [0..1]`；`requirementsForMaintenanceOfListeningWatch: text [1..1]` | `GM_OrientableSurface [0..*]` |
| `LocalPortServiceArea` | `serviceAccessProcedure: text [0..1]`；`requirementsForMaintenanceOfListeningWatch: text [1..1]` | `GM_OrientableSurface [0..*]` |
| `PiracyRiskArea` | `restriction: restriction [1..*]`；`status: status [0..*]` | `PointOrSurface [1..*]`，按当前目录；原图标注为面 |
| `UnderkeelClearanceManagementArea` | `dynamicResource: dynamicResource [1..1]` | `GM_OrientableSurface [1..*]` |
| `PlaceOfRefuge` | `status: status [0..*]`；`communicationChannel: text [0..*]` | `PointOrSurface [1..*]` |
| `PilotService` | `categoryOfPilot: categoryOfPilot [0..*]`；`pilotQualification: pilotQualification [0..1]`；`pilotRequest: text [0..1]`；`remotePilot: Boolean [1..1]`；`noticeTime: noticeTime [0..1]` | `GM_OrientableSurface [0..*]` |

### 4.1 定线措施的条件约束

图中给出了两项独立约束，两项都必须成立。

```text
categoryOfTrafficSeparationScheme 未填写
    或 categoryOfRouteingMeasure = 5

categoryOfNavigationLine 未填写
    或 categoryOfRouteingMeasure = 4
```

因此填写 `categoryOfTrafficSeparationScheme` 时，`categoryOfRouteingMeasure` 必须为 `5`；填写 `categoryOfNavigationLine` 时，后者必须为 `4`。两种细分类属性同时填写会造成冲突。具体代码的标签仍须查采用版本的目录。

### 4.2 几何缺失与关联例外

图中明确允许某要素自身没有几何，但与一个具有几何的要素实例关联。执行时按以下顺序处理。

1. 检查本类允许的几何类型和数量。
2. 自身有几何时，记录坐标来源、坐标参考系、轴序及准确性确认状态。
3. 自身无几何时，检查是否存在目录允许的有效关联，且目标实例实际具备几何。只有关联路径及其空间适用含义均可确认时，才将其标记为“通过关联定位”。
4. 单纯指向机构或另一无几何实例的引用，不能证明空间定位完整。
5. 对图示几何最小数量与该注记之间的适用关系，依据采用版本的完整规范和验证规则判断。记录所用例外，避免直接把“无自身几何”一律判为合格或错误。
6. 无自身几何且无法确认关联定位时，列为几何待补项，请用户提供准确范围或同名要素 GML。
7. 示意图勾绘或概位几何标记为待确认。多面、多线及不同几何实例必须保留，禁止擅自压缩成一个对象。

### 4.3 图中画出的关联

下表按“源对象 → 目标对象”描述角色。数量表示每一个源对象允许指向的目标数量。

| 关联类 | 源对象 | 角色 | 目标对象 | 目标数量 |
| --- | --- | --- | --- | --- |
| `TrafficControlServiceAggregation` | `VesselTrafficServiceArea`、`ShipReportingServiceArea`、`LocalPortServiceArea` | `consistsOf` | `RadioCallingInPoint`、`SignalStationWarning`、`RadarRange`、`SignalStationTraffic` | 合计 `0..*` |
| `TrafficControlServiceAggregation` | 上行组成部分 | `componentOf` | 上行交通服务区 | `0..1` |
| `PilotageDistrictAssociation` | `PilotageDistrict` | `consistsOf` | `PilotBoardingPlace` | `0..*` |
| `PilotageDistrictAssociation` | `PilotBoardingPlace` | `componentOf` | `PilotageDistrict` | `0..1` |
| `ServiceProvisionArea` | `PilotService` | `serviceArea` | `PilotageDistrict` | `0..1` |
| `ServiceProvisionArea` | `PilotageDistrict` | `serviceProvider` | `PilotService` | `0..*` |
| `ServiceProvisionArea` | `PilotService` | `serviceArea` | `PilotBoardingPlace` | `0..*` |
| `ServiceProvisionArea` | `PilotBoardingPlace` | `serviceProvider` | `PilotService` | `0..*` |

三张图没有完整展开地理要素与信息要素之间的关联角色。`Authority`、`ShipReport`、`Applicability`、`ContactDetails` 等对象的关联，应加载所选版本的关联定义后再编码。创建对象时分配稳定标识，引用必须解析到真实存在的目标，类及角色必须兼容。

## 5. 信息要素的本类字段

| 类 | 本类字段 |
| --- | --- |
| `AbstractRxN`，抽象 | `categoryOfAuthority: categoryOfAuthority [0..1]`；`textContent: textContent [0..*]`；`graphic: graphic [0..*]`；`rxnCode: rxnCode [0..*]` |
| `NauticalInformation` | 图中无新增字段，继承 `AbstractRxN` |
| `Regulations` | 图中无新增字段，继承 `AbstractRxN` |
| `Restrictions` | 图中无新增字段，继承 `AbstractRxN` |
| `Recommendations` | 图中无新增字段，继承 `AbstractRxN` |
| `Authority` | `categoryOfAuthority: categoryOfAuthority [1..1]`；`textContent: textContent [0..1]` |
| `ShipReport` | `categoryOfShipReport: categoryOfShipReport [1..*]`；`imoFormatForReporting: Boolean [1..1]`；`noticeTime: noticeTime [1..*]`；`textContent: textContent [0..1]` |
| `NonStandardWorkingDay` | `dateFixed: S100_TruncatedDate [0..*]`；`dateVariable: text [0..*]`；`information: information [0..*]` |
| `ServiceHours` | `scheduleByDayOfWeek: scheduleByDayOfWeek [1..*]`；`information: information [0..*]` |
| `Applicability` | `inBallast: Boolean [0..1]`；`categoryOfCargo: categoryOfCargo [0..*]`；`categoryOfDangerousOrHazardousCargo: categoryOfDangerousOrHazardousCargo [0..*]`；`categoryOfVessel: categoryOfVessel [0..1]`；`categoryOfVesselRegistry: categoryOfVesselRegistry [0..1]`；`logicalConnectives: logicalConnectives [0..1]`；`thicknessOfIceCapability: Integer [0..1]`；`vesselPerformance: text [0..1]`；`information: information [0..*]`；`vesselsMeasurements: vesselsMeasurements [0..*]` |
| `ContactDetails` | `callName: text [0..1]`；`callSign: text [0..1]`；`categoryOfCommPref: categoryOfCommPref [0..1]`；`communicationChannel: text [0..*]`；`contactInstructions: text [0..1]`；`language: ISO639-3 [0..1]`；`mMSICode: text [0..1]`；`contactAddress: contactAddress [0..*]`；`frequencyPair: frequencyPair [0..*]`；`information: information [0..*]`；`onlineResource: onlineResource [0..*]`；`telecommunications: telecommunications [0..*]`；`radiocommunications: radiocommunications [0..*]` |

同名字段在不同类中的数量可能不同。例如 `PilotService.noticeTime` 最多一个，`ShipReport.noticeTime` 至少一个；`RadioCallingInPoint.categoryOfVessel` 可重复，`Applicability.categoryOfVessel` 最多一个。校验必须带上所属类型。

## 6. 复合属性结构

### 6.1 名称和时间

| 复合属性 | 子属性 | 类型 | 数量 |
| --- | --- | --- | --- |
| `featureName` | `displayName` | `Boolean` | `0..1` |
| `featureName` | `language` | `ISO639-3` | `0..1` |
| `featureName` | `name` | `text` | `1..1` |
| `fixedDateRange` | `dateStart` | `S100_TruncatedDate` | `0..1` |
| `fixedDateRange` | `dateEnd` | `S100_TruncatedDate` | `0..1` |
| `periodicDateRange` | `dateStart` | `S100_TruncatedDate` | `1..1` |
| `periodicDateRange` | `dateEnd` | `S100_TruncatedDate` | `1..1` |

抽取约定如下。

- `featureName.name` 提取对象正式名称。保留原文中的地域和序号，新增地域前缀须有来源依据。
- 每种语言的名称独立记录。人工补译的英文名称标记为翻译，并保留中文原名证据。
- `displayName` 描述名称显示选项，其真值由编码约定决定。
- `fixedDateRange` 表达明确的固定起止日期，允许只提取已知的一端。
- `periodicDateRange` 表达周期性的起止日期，其内部两端均为必填。周期重复的具体日期表达须查 `S100_TruncatedDate` 定义。
- 文件发布日期与规定适用的起止日期分别抽取。时间无法确定时登记缺失，保留原文。

### 6.2 资料来源

| `sourceIndication` 子属性 | 类型 | 数量 |
| --- | --- | --- |
| `categoryOfAuthority` | `categoryOfAuthority` | `0..1` |
| `countryName` | `text` | `0..1` |
| `reportedDate` | `S100_TruncatedDate` | `0..1` |
| `source` | `text` | `0..1` |
| `sourceType` | `sourceType` | `0..1` |
| `featureName` | `featureName` | `0..*` |

图中黄色注记明确规定，`sourceIndication.featureName` 的内容是来源机构的名称。

- 对象名称写入对象自己的 `featureName`。发布机构名称写入 `sourceIndication.featureName`。
- `source` 使用可核实的文件编号或文件标题。双语文本如何合并为单值，遵循项目约定。
- 按当前项目的来源约定，`reportedDate` 提取原文件可核实的发布日期，缺失时省略。
- `countryName`、机构类别和来源类别均需要原文或可核实的元数据支持。
- 多来源文件必须记录到对应对象或支持来源属性的复合属性中，并保持每项事实的证据指向。

### 6.3 文本内容和信息

| 复合属性 | 子属性 | 类型 | 数量 |
| --- | --- | --- | --- |
| `textContent` | `categoryOfText` | `categoryOfText` | `0..1` |
| `textContent` | `information` | `information` | `0..*` |
| `textContent` | `onlineResource` | `onlineResource` | `0..1` |
| `textContent` | `sourceIndication` | `sourceIndication` | `0..1` |
| `information` | `fileLocator` | `text` | `0..1` |
| `information` | `fileReference` | `text` | `0..1` |
| `information` | `headline` | `text` | `0..1` |
| `information` | `language` | `ISO639-3` | `0..1` |
| `information` | `text` | `text` | `0..1` |

- 保持层级 `textContent → information → text`。
- `Applicability`、`ServiceHours` 等直接声明 `information` 的类按本类结构填写。
- `headline` 提取或概括该段主题；生成的概括应在证据记录中标记。
- `text` 保留适用对象、条件、数值、单位、例外及时间限制。
- 图中各子属性多为可选。创建复合属性实例时至少应有实际信息，避免生成空壳对象。
- `fileReference`、`fileLocator` 按规范表达引用文件及文件内定位。证据记录中的页码可单独保存，不能仅凭字段英文名随意写入 GML。

### 6.4 在线资源

| `onlineResource` 子属性 | 类型 | 数量 |
| --- | --- | --- |
| `linkage` | `URL` | `1..1` |
| `protocol` | `text` | `0..1` |
| `applicationProfile` | `text` | `0..1` |
| `nameOfResource` | `text` | `0..1` |
| `onlineResourceDescription` | `text` | `0..1` |
| `onlineFunction` | `onlineFunction` | `0..1` |
| `protocolRequest` | `text` | `0..1` |

已有有效链接时才创建该实例。单个 `textContent` 内最多一个 `onlineResource`，`ContactDetails` 内允许多个。

### 6.5 富余水深

| `underkeelAllowance` 子属性 | 类型 | 数量 |
| --- | --- | --- |
| `underkeelAllowanceFixed` | `Real` | `0..1` |
| `underkeelAllowanceVariableBeamBased` | `Real` | `0..1` |
| `underkeelAllowanceVariableDraughtBased` | `Real` | `0..1` |
| `operation` | `operation` | `0..1` |

图示条件为：三个 allowance 子属性中填写超过一个时，`operation` 必填。具体操作符和单位应查目录，原文包含复杂条件时写入所属要素允许的 `textContent`，同时保留可以结构化的参数。

### 6.6 图形、方位和分类代码

| 复合属性 | 子属性 | 类型 | 数量 |
| --- | --- | --- | --- |
| `graphic` | `pictorialRepresentation` | `text` | `1..*` |
| `graphic` | `pictureCaption` | `text` | `0..1` |
| `graphic` | `sourceDate` | `Date` | `0..1` |
| `graphic` | `pictureInformation` | `text` | `0..1` |
| `graphic` | `bearingInformation` | `bearingInformation` | `0..1` |
| `bearingInformation` | `cardinalDirection` | `cardinalDirection` | `0..1` |
| `bearingInformation` | `distance` | `Real` | `0..1` |
| `bearingInformation` | `sectorBearing` | `Real` | `0..2`，有序 |
| `bearingInformation` | `information` | `information` | `0..*` |
| `bearingInformation` | `orientation` | `orientation` | `0..1` |
| `orientation` | `orientationUncertainty` | `Real` | `0..1` |
| `orientation` | `orientationValue` | `Real` | `1..1` |
| `rxnCode` | `categoryOfRxN` | `categoryOfRxN` | `0..1` |
| `rxnCode` | `actionOrActivity` | `actionOrActivity` | `0..1` |
| `rxnCode` | `headline` | `text` | `0..1` |

图件中的方位应保留原始基准、单位及边界顺序。`orientation.orientationValue` 是复合属性内部必填单值，`RadioCallingInPoint.orientationValue` 为本类可选最多两个值，两者分别校验。

### 6.7 通信

| 复合属性 | 子属性 | 类型 | 数量 |
| --- | --- | --- | --- |
| `telecommunications` | `categoryOfCommPref` | `categoryOfCommPref` | `0..1` |
| `telecommunications` | `contactInstructions` | `text` | `0..1` |
| `telecommunications` | `telecomCarrier` | `text` | `0..1` |
| `telecommunications` | `telecommunicationIdentifier` | `text` | `1..1` |
| `telecommunications` | `telecommunicationService` | `telecommunicationService` | `0..*` |
| `telecommunications` | `scheduleByDayOfWeek` | `scheduleByDayOfWeek` | `0..1` |
| `radiocommunications` | `categoryOfCommPref` | `categoryOfCommPref` | `0..1` |
| `radiocommunications` | `categoryOfMaritimeBroadcast` | `categoryOfMaritimeBroadcast` | `0..*` |
| `radiocommunications` | `categoryOfRadioMethods` | `categoryOfRadioMethods` | `0..*` |
| `radiocommunications` | `communicationChannel` | `text` | `0..*` |
| `radiocommunications` | `contactInstructions` | `text` | `0..1` |
| `radiocommunications` | `signalFrequency` | `Integer` | `0..*` |
| `radiocommunications` | `frequencyPair` | `frequencyPair` | `0..*` |
| `radiocommunications` | `timeIntervalsByDayOfWeek` | `timeIntervalsByDayOfWeek` | `0..*` |

呼号、呼叫名称、MMSI、频道、电话号码和频率分别抽取。通信时间表跟随对应的通信方式，避免把某一电话或无线电业务的值班时段错误推广到全部机构服务。

### 6.8 图中未展开的类型

下列类型已被引用，但三张图未提供内部字段。使用前必须加载其正式定义。

| 类型 | 在图中的使用位置 |
| --- | --- |
| `noticeTime` | `ShipReport`、`PilotService` |
| `scheduleByDayOfWeek` | `ServiceHours`、`telecommunications` |
| `timeIntervalsByDayOfWeek` | `radiocommunications` |
| `vesselsMeasurements` | `Applicability` |
| `contactAddress` | `ContactDetails` |
| `frequencyPair` | `ContactDetails`、`radiocommunications` |

图中的 `S-127 Geometry` 和 `S-127 Metadata` 包同样未展开。坐标参考系、轴序、几何拓扑、元数据内容及完整 GML 序列化须查相应定义。

## 7. 原文提取的操作约定

本节给出供 Agent 执行的提取步骤。类的正式语义和专题编码仍以完整规范及项目规则为依据。

### 7.1 按条款建立对象清单

逐条识别正式命名的水域、航路、报告点、报告线、服务区、机构、报告类型和船舶条件。分别记录要素名称、候选类、来源位置、适用条件、时间和几何依据。

原文分别命名的对象保持独立。原文没有声明统一范围或共同对象时，应保留区别。名称相同的不同空间实例必须使用不同标识。

### 7.2 按信息性质选择候选类

该表用于确定候选类。实际选型结合 10、11 的对应章节和 05 的专题约定；普通受限航道按当前项目规则选择 RestrictedAreaNavigational。

| 原文信息 | 候选类或属性 | 判断要点 |
| --- | --- | --- |
| 报告点、报告线及交通流方向 | `RadioCallingInPoint` | 核对交通流、方向和点线几何 |
| 航警信号、交通信号、雷达覆盖范围 | `SignalStationWarning`、`SignalStationTraffic`、`RadarRange` | 分清对象功能 |
| 明确的船舶定线措施 | `RouteingMeasure` | 必须能确定定线措施类别，普通“航道”名称本身不足以确定 |
| 航行限制、其他监管限制 | `RestrictedAreaNavigational`、`RestrictedAreaRegulatory` | 查限制语义与具体类允许的枚举子集 |
| VTS、报告制及本地港口服务范围 | 对应的服务区类 | 分清服务范围、服务内容和报告要求 |
| 引航区、引航登离船位置、引航服务 | `PilotageDistrict`、`PilotBoardingPlace`、`PilotService` | 分清区域、登离船位置与服务对象 |
| 富余水深要求与动态管理水域 | 两类 underkeel 区域 | 分清 allowance 要求与 management 服务 |
| 主管或服务机构 | `Authority` | 确认机构身份和类别 |
| 强制法规、限制条款、建议及航海信息 | `Regulations`、`Restrictions`、`Recommendations`、`NauticalInformation` | 保留原文法律或建议语气 |
| 应报告的时机、内容和格式 | `ShipReport` | 提取报告类别、是否采用 IMO 格式及通知时间 |
| 船舶类别、载货、尺度、船籍和组合条件 | `Applicability` | 保留“且”“或”及适用范围，尺度结构查 `vesselsMeasurements` |
| 联系方式 | `ContactDetails` | 保留联系方式归属和通信方式 |
| 值班、服务时间及例外工作日 | `ServiceHours`、`NonStandardWorkingDay` | 保留时区、星期、时段及例外 |

### 7.3 分离事实与补充判断

- 原文明确出现的值标记为 `extracted`。
- 基于原文作出的分类或归纳标记为 `inferred`，说明理由。
- 翻译标记为 `translated`，同时保留原文。
- 已知需填写但证据缺失的值标记为 `missing`。
- 存在多个解释、多个同名候选或几何待确认时标记为 `needs_confirmation`。

这些状态属于抽取记录，最终进入 GML 的属性必须经过对应的规范校验。

### 7.4 保留限制和例外

“禁止”“限制”“必须”“建议”“可以”分别保留。数值限制连同单位、比较关系和船舶条件一起提取。条款引用其他法规时，记录引用关系及可核实内容，进一步补充的内容必须有对应来源。

同一属性代码在不同类中的允许范围可能不同。项目若明确采用超出当前内置目录的编码，记录采用依据并在报告中标注兼容性差异。

## 8. Agent 输出约定

分别保存 `featureset.json`、`evidence.json` 和 `pending.json`。其中 `featureset.json` 直接交给项目校验器和构建器，另两个文件通过 `featureRef` 关联要素，供审核和后续补齐使用。

### 8.1 项目字段映射

| 模型内容 | featureset.json 表示 |
| --- | --- |
| 要素标识 | `features[].ref`，数据集内唯一 |
| 要素类 | `features[].type` |
| 名称、固定日期、来源、文本内容 | 对应要素的 `featureName`、`fixedDateRange`、`sourceIndication`、`textContent` |
| 本类专有属性 | `features[].attributes` 中的规范字段名，例如 `restriction` |
| 坐标定义 | 顶层 `geometries` 对象，以几何键名索引 |
| 几何引用 | `features[].geometry` 填已有几何键名字符串，例如 `g-area-001` |
| 几何缺失 | 省略该要素的 `geometry`，在 `pending.json` 登记 |
| 暂定几何 | 保留几何引用，在顶层 `geometryReview` 中按要素 `ref` 记录待确认原因，供现有几何流程读取 |
| 关联 | `features[].associations` 内填写 `role` 与 `target`，目标为另一要素的 `ref` |
| 出处条款 | 可在要素的 `clause` 保存，详细证据写入 `evidence.json` |

`FeatureName`、`Language`、`Geometry` 等大写展示标签在 JSON 中分别对应 `featureName`、`language`、`geometry`。日期使用项目 Schema 接受的表示方式，当前完整日期为 `YYYY-MM-DD`。原文只给年或年月而当前字段仅接受完整日期时，保留原始精度到证据与待补清单，待确认后再填入完整日期。

模型中允许重复的属性按当前 Schema 的对象、数组或双语简写表示。当前 Schema 无法承载的字段或多几何情况应记录为待处理项，保留完整原始数据，再核对规范与实现。几何状态、原文证据和待补问题写入旁文件。

### 8.2 featureset.json 示例

下例表示具有禁锚规定、尚缺边界坐标的初始要素。数据集名称和英文名称为演示值，实际任务必须使用有依据的值。

```json
{
  "dataset": {
    "name": "127CN00EXAMPLE001",
    "agency": "CN",
    "updateNumber": 0,
    "featureIdStart": 1
  },
  "geometries": {},
  "features": [
    {
      "ref": "area-001",
      "type": "RestrictedAreaNavigational",
      "featureName": {
        "eng": "Example Waters",
        "zho": "示例水域"
      },
      "attributes": {
        "restriction": ["anchoring prohibited"]
      },
      "clause": "第六条",
      "associations": []
    }
  ]
}
```

该初稿按 `SKILL.md` 的三阶段流程使用 `build_gml.py --draft` 构建。取得准确几何后，使用 `geometry_workflow.py merge` 合并，或在 `geometries` 中保存定义并填写 `features[].geometry` 引用，再运行校验与构建流程。

### 8.3 evidence.json 示例

```json
{
  "featureset": "featureset.json",
  "evidence": [
    {
      "featureRef": "area-001",
      "field": "attributes.restriction[0]",
      "value": "anchoring prohibited",
      "status": "extracted",
      "source": {
        "file": "示例通告.pdf",
        "page": 3,
        "clause": "第六条",
        "quote": "示例水域内禁止锚泊。"
      }
    }
  ]
}
```

### 8.4 pending.json 示例

```json
{
  "featureset": "featureset.json",
  "pending": [
    {
      "featureRef": "area-001",
      "field": "geometry",
      "status": "missing",
      "reason": "来源未提供边界坐标，尚无可确认的关联定位对象",
      "request": "请提供准确边界或包含同名要素几何的 GML"
    }
  ]
}
```

几何清单逐项覆盖全部地理要素，状态至少区分 `provided`、`via_association`、`needs_confirmation`、`missing`。信息要素可单列为“不适用”。精确几何、关联定位及概位几何的依据都应可追溯。旁文件采用这些状态，`geometry_workflow.py inventory` 当前输出的清单状态为已提供、缺失、待确认或不适用。关联定位例外需要在报告中补充目标 `ref`、角色、规范依据和核验结论，现有脚本对无自身几何的提示仍需结合该记录复核。

### 8.5 结果表格

回复中的要素编码结果表和导出的 Excel、CSV 均使用以下八列，每个要素占一行，保持 `features` 原顺序。

| 要素类 | FIN | 中文名称 | 英文名称 | 编码信息 | 有效期起 | 有效期止 | 来源 |
| --- | --- | --- | --- | --- | --- | --- | --- |

- 要素类取 `features[].type`，中文与英文名称分别按 `zho`、`eng` 提取，缺失语言留空。
- FIN 对应同一份要素清单本次构建的 GML。默认从 `dataset.featureIdStart` 开始，只对地理要素依序递增，分区默认使用 `1`，显示格式为 `编号-分区`。显式指定的 `attributes["s100:featureObjectIdentifier"]` 按其实际编号及分区显示。信息要素显示 `---`。历史 GML 反解可能无法保留原始不连续编号，核对历史 FIN 时应读取原 GML。
- 编码信息汇总 `features[].attributes` 中的专有属性。枚举查目录后显示为 `restriction=3(fishing prohibited)` 等格式。多值使用分号和单元格内换行，嵌套复合属性保留路径，重复的复合实例保留索引。布尔值显示 `true` 或 `false`，其他值保留实际内容。表中代码与本次 GML 输出一致，目录子集差异另在检查报告说明。
- 有效期起、有效期止取本次构建实际采用的 `fixedDateRange.dateStart` 和 `dateEnd`。完整日期采用 `YYYY-MM-DD`，没有依据时留空。
- 来源取本次构建实际采用的 `sourceIndication.source`，双语来源按先英后中展示。
- 日期及来源先使用逐要素显式值，未指定时按 `reusable` 设置决定是否继承 `common`。显式 `null` 会抑制继承。
- Excel 表头为蓝底白字，正文自动换行、细边框、冻结首行。`文件解析`、`gml校对`、`gml审核` 三表使用相同列序并保留完整来源。
- 条款出处和关联保存到 Excel 来源格批注，审核意见可追加到批注。CSV 的条款及证据通过对应 `featureset.json` 与 `evidence.json` 追溯。几何状态使用单独的几何清单。

## 9. 交付前检查清单

- [ ] 每个对象使用具体类，继承字段完整且没有错误添加其他类的字段。
- [ ] 已按 10、11 核对类型含义、适用范围、相近类边界和当前项目口径，并记录类型判断证据。
- [ ] 字段基数按“所属类 + 属性”检查，必填缺失已登记。
- [ ] 所有枚举值、代码及所属类允许子集与采用目录一致。
- [ ] `RouteingMeasure` 的两项条件约束同时满足。
- [ ] 多个 allowance 值并存时填写了有依据的 `operation`。
- [ ] 名称、来源机构名称及来源文件标题分别放入正确位置。
- [ ] 发布时间、有效日期、周期日期和通知时间分别提取。
- [ ] 所有文本保留适用主体、条件、例外、数值和单位。
- [ ] 几何类型及数量符合规则，无几何实例的关联例外有明确依据。
- [ ] 标识唯一、引用可解析、关联角色和目标类兼容，双向关系无矛盾。
- [ ] 无法从图中确定的类型内部字段与枚举已查正式定义。
- [ ] 每一项提取、推断和翻译均保留证据或判断依据。
- [ ] 检查报告明确列出未补数据、版本差异和未执行的核验项。

## 10. 可直接用于 Agent 的任务指令

> 阅读本规则及任务指定版本的要素目录和专题编码规则。从用户上传的文本与附件中逐条识别对象，选择具体要素类，并按照继承关系、字段类型、数量和条件约束提取结构化数据。每个值保留原文证据，分类推断和翻译单独标记。对来源缺失、必填字段缺失、同名对象歧义及几何缺失生成逐项待补清单。只有关联角色和空间适用关系均有效时，才使用无自身几何的关联定位例外。分别输出项目 Schema 支持的 featureset.json、evidence.json、pending.json 和检查报告，再调用项目序列化程序生成 GML。
