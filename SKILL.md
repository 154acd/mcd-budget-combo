---
name: mcd-budget-combo
description: 麦当劳预算饱腹搭配。当用户给出位置和预算（默认 30 元）、想找附近麦当劳门店时使用，例如「30 元怎么吃麦当劳最饱」「附近麦当劳怎么点最划算」「我在 XX 附近只有 XX 元」「麦当劳优惠券怎么用」「预算内麦当劳搭配」。基于麦当劳官方 MCP Server 实时查询附近门店、各门店真实可用优惠券与在售菜单，通过 calculate-price 接口对候选搭配逐一真实核价，输出预算内最饱腹、件数最多、经典省钱、用券多吃等多种搭配方案。默认只读，不代替用户下单；写操作（领券/下单/存地址）必须先复述并获得用户确认后才执行。触发词：30元麦当劳、麦当劳吃饱、预算搭配、麦当劳套餐推荐、麦当劳省钱、附近麦当劳、麦当劳优惠券、mcd budget combo。
agent_created: true
license: MIT
---

# 麦当劳预算饱腹搭配（mcd-budget-combo）

## 这个 Skill 解决什么问题

用户来到一个地点、带着有限预算（默认 30 元），想知道附近哪家麦当劳、用什么券、点什么能吃得最饱。难点有三：各门店菜单和券不同；券面文字不等于真实优惠（门槛、渠道、指定商品都会影响）；价格必须真算。本技能把「找店 → 查券 → 查菜单 → 枚举搭配 → 真实核价 → 输出报告」压缩成一轮对话，为每家附近门店输出一份搭配报告。

## 何时使用

- 用户给出「位置 + 预算」（预算未说时默认 30 元）；
- 用户询问附近麦当劳怎么点最划算、怎么用券、怎么吃饱；
- 用户粘贴地图复制的详细地址并给出预算。

不适用：积分商城兑换、积分抽奖、派对预订、企业团餐——这些不是本技能场景，不要强行套用。

## 前置条件

mcd-mcp 连接器已配置并启用（Streamable HTTP，url 为 https://mcp.mcd.cn）。未配置时明确告知用户前往 open.mcd.cn/mcp 申请 Token，并停止后续调用。**任何情况下不得编造门店、价格或优惠券。**

## 核心工作流（7 步）

### 第 1 步：拆解位置与预算

- 位置支持三种输入：① 地图复制的详细地址（实测可原样直贴作 keyword，13m 级精度）；② 城市 + 区/商圈/地标关键词；③ 口语化位置描述。
- 从位置中识别 city（城市名），其余部分作为 keyword。city 识别不出时追问一次「你在哪个城市」。
- 预算未说明默认 30 元。用户说「送到家 / 点外卖」时进入外送分支（见下文）。

### 第 2 步：获取当前时间

调用 now-time-info 一次并缓存于会话，用于券有效期与营业状态判断。时间不靠模型记忆。

### 第 3 步：查附近门店

调用 query-nearby-stores：searchType=2、city、keyword、beType=1（到店自取）。返回已按 distance 升序，直接取前 5 家（硬上限）。向用户展示店名 / 距离 / 营业状态。

### 第 4 步：逐店查真实可用券

对每家门店调用 query-store-coupons（storeCode + orderType=1 + beType=1）。这是门店可用券的唯一权威来源。query-my-coupons 不校验门店，不得将其结果当作「本店可用券」展示。

### 第 5 步：逐店拉菜单

对每家门店调用 query-meals（storeCode + orderType=1 + beType=1），把返回的 data 对象用 Write 工具落盘为 menu.json 供第 6 步的规划脚本读取（工具返回会先进入上下文，落盘的用途是供脚本读取而非节省上下文）。可只保留规划脚本需要的字段——`categories[].meals[].code/tags` 与 `meals{}` 的 `name/currentPrice/canWithOrder/discountType`，其余字段（image、withOrder、originalPrice 等）可裁剪，裁剪不影响结果。

### 第 6 步：枚举候选 + 真实核价

1. 运行 `python scripts/combo_planner.py plan --menu menu.json --budget <预算>`，按需附加 `--coupons coupons.json`（第 4 步的券）与 `--nutrition nutrition.json`，得到不超过 6 个候选；
2. 对每个候选调用 calculate-price 真实核价（用券时在对应 item 上传 couponId 与 couponCode）；
3. 核价超预算的候选丢弃，取同策略下一个候选；用核价结果替换目录价，重算件数 / 主食数 / 剩余额度；
4. 需要展示千卡时，先调用一次 list-nutrition-foods 建立「名称 → 千卡」表并传给 --nutrition；未覆盖的商品标注「营养数据暂缺」，不得估算编造。

### 第 7 步：输出报告

每家门店按 references/combo-strategy.md 第 4 节的模板输出一块报告，结尾给出「领券 / 就点 X 方案」引导语。

## 外送（麦乐送）分支

用户说「送到家 / 点外卖」时：

1. 调用 delivery-query-addresses 取地址簿：有地址则请用户选一个；无地址则引导用户提供六要素（城市 / 姓名 / 性别 / 电话 / 地址 / 门牌号），**仅当用户明确说「保存这个地址」时**才调用 delivery-create-address，调用前复述六要素；
2. 调用 delivery-query-stores 得到可配送门店（含 beCode）；
3. 后续 query-store-coupons / query-meals / calculate-price 一律使用 orderType=2 + beType=2 + beCode；
4. 先用一个候选组合调用一次 calculate-price 探测配送费 + 打包费，从预算中扣除后再枚举；配送费 + 打包费超过预算 30% 时，在报告中显著提示「外送费占比过高，建议到店自取」，并附到店方案对比。

## 工具调用规范

| 规范 | 要求 |
| --- | --- |
| 调用编排 | 无依赖关系的调用（如多家门店各自的 query-store-coupons / query-meals）可在同一步骤内并行；有依赖关系的（先取门店再查券、先探测配送费再枚举）必须串行。复用会话内已获取的结果，不做无意义重复查询 |
| 单位归一化 | 菜单 currentPrice 是「元」；calculate-price 返回全部是「分」，展示一律 ÷100 换算成元 |
| 调用预算 | 门店 ≤ 5 家；单店 calculate-price ≤ 6 次——按策略顺序核价，某候选超预算即换用 planner 输出的下一个候选；候补耗尽仍无足够方案时，以已通过的最优方案输出并说明差额，不得为凑数编造 |
| 文件路径 | 规划脚本的 --menu / --coupons / --nutrition 一律传 Windows 绝对路径（如 D:\work\menu.json）；运行脚本使用本机 Python 可执行文件的绝对路径，不要依赖 /tmp 等 Unix 路径或相对路径 |
| 限流与鉴权 | 429 退避 3 秒重试 1 次，仍失败如实告知；401 引导用户重新激活 Token |
| beType 矩阵 | 到店自取 beType=1 不传 beCode；麦乐送 beType=2 必传 beCode（来自 delivery-query-stores）。参数细节见 references/mcd-tools.md |
| 地址直贴 | keyword 可直接传地图复制的完整地址行，含「省 / 市」前缀不出错，无需裁剪 |
| 券商品匹配 | 券的适用商品必须按 productCode 与菜单精确匹配，禁止按餐品名称人工放行 |

## 安全红线（不可违反）

1. **默认只读**：禁止主动调用 auto-bind-coupons、create-order、delivery-create-address 或任何写操作工具；
2. 仅当用户明确说「领券 / 下单 / 保存地址」时才考虑写工具，且必须先复述（可领券列表 / 订单内容与金额 / 地址六要素）→ 等待用户确认 → 才执行；
3. 禁止编造接口未返回的门店、价格、券、场次；一切优惠效果以 calculate-price 实时核价为准；
4. 随单购商品（discountType 为「随单购麦金卡优惠」或 canWithOrder=true）默认排除；如用户明确持有麦金卡，可单独成类并在方案中标注「需随单购麦金卡」；
5. 周边商品（帽 / 毛绒 / 痒痒挠等）与酱料不进候选池；
6. 券商品匹配必须按 productCode 精确匹配，禁止按名称人工放行；匹配不上即判定「暂不可用」；
7. 不得输出或存储真实 Token、账号信息。

## 输出格式硬性要求

1. 每个价格必须标注来自 calculate-price 实时核价，不得用菜单目录价充当最终价；
2. 优惠券必须标明「可用」或「暂不可用（适用商品不在售）」；
3. 外送场景配送费与打包费单独列示；
4. 无千卡数据时不出现千卡字段；有数据时标注「约 NNN 千卡（M/M 项有数据）」；
5. 报告模板原文见 references/combo-strategy.md 第 4 节，按原样套用；任一策略无产出时使用该节规定的固定「无（…）」文案；
6. 门店行必须结合 now-time-info 与门店营业起止时间，标注当前是否处于营业时段（如「营业中 · 当前为夜市时段」）；
7. 「加 X 元可得 XX」的差额引导引用菜单目录价即可，但必须标注「目录价，下单前以实时核价为准」；其余所有方案价格必须来自 calculate-price 核价。

## 异常处理

| 情况 | 处理 |
| --- | --- |
| 位置拆不出 city / keyword | 追问一次；用户只给城市则用城市名兜底并说明 |
| 附近无门店 | 如实告知，建议扩大范围或更换地点 |
| 预算低于门店最低在售价 | 报出最低价商品与差额，给「加 X 元可得 XX」建议，不硬凑 |
| 门店无可用券 | 输出无券基线方案；提示 available-coupons 有可领之券，询问是否「领券」 |
| 券适用商品不在菜单 | 标注「该券当前不可用」，不计入方案 |
| 核价结果全部超预算 | 输出最接近的方案并说明差额 |
| 429 / 401 / 接口失败 | 按工具调用规范处理，如实告知，禁止编造数据填补 |

## 参考资料使用指引

- 动手写方案前必读 references/combo-strategy.md（分类规则、枚举模板、输出模板）；
- 记不清工具参数、单位、坑位时读 references/mcd-tools.md；
- 分工原则：SKILL.md 只放「何时必须做什么」的硬规则；字段级细节全部放在 references。

## 自检与维护

- 修改 scripts/combo_planner.py 后，必须运行 `python scripts/combo_planner.py selftest` 且全部通过再交付；
- 本目录即技能目录，可整体复制到 ~/.workbuddy/skills/mcd-budget-combo 完成安装。
