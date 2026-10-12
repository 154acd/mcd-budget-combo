# mcd-mcp 工具速查表（mcd-budget-combo 专用）

> 本文件是技能的按需参考资料。SKILL.md 只规定「何时必须做什么」；字段级细节、参数矩阵、坑位清单以本文件为准。所有结论均来自 2026-10-12 对官方 MCP 的真实调用实测。

## 0. 通用铁律（先读再调）

1. **工具全名**：`mcp__mcd-mcp__<tool-name>`（如 `mcp__mcd-mcp__query-nearby-stores`）。
2. **调用编排**：无依赖关系的调用（如多家门店各自的 query-store-coupons / query-meals）可在同一步骤内并行；有依赖关系的（先取门店再查券、先探测配送费再枚举）必须串行。复用会话内已获取的结果，不做无意义重复查询。限流约 600 次/分钟/Token；遇 429 退避 3 秒重试 1 次，仍失败则如实告知用户。
3. **单位归一化（最易错）**：
   - `query-meals` 的 `currentPrice` / `originalPrice` 是「**元**」字符串（如 `"14.9"`）；
   - `calculate-price` 返回的价格字段全部是「**分**」（如 `2880` = 28.80 元）；
   - 向用户展示时一律换算成元，保留两位小数。
4. **storeCode 必须纯数字**；beCode 与 storeCode 配对使用，按下方矩阵传。
5. **不编造**：任何门店、价格、券、场次必须以工具返回为准；接口没返回就明说「暂未获取到」。

## 1. beType / orderType / beCode 矩阵（最高频错误点）

| 场景 | beType | orderType | beCode | 门店来源工具 |
|---|---|---|---|---|
| 到店自取 | 1 | 1 | **不传** | query-nearby-stores(beType=1) |
| 得来速车道取餐 | 5 | 1 | 必传（来自 query-nearby-stores 返回的 beCode） | query-nearby-stores(beType=5) |
| 麦乐送到家 | 2 | 2 | 必传（来自 delivery-query-stores 的 beCode） | delivery-query-stores |
| 企业团餐 | 6 | 2 | 必传（来自 delivery-query-stores 的 beCode） | delivery-query-stores |

> query-store-coupons / query-meals / calculate-price 三个工具共用同一套 orderType+beType+beCode 规则，必须与用户选择的取餐方式一致，不能混搭。

## 2. 只读工具详解

### 2.1 query-nearby-stores — 查附近门店（到店主链路第一步）

- **用途**：按位置搜索附近可点餐门店。
- **必填参数**：`searchType=2`（按位置搜索；1 是搜收藏餐厅，不要用）、`city`（城市名）、`keyword`（位置关键词）、`beType`（1 到店自取 / 5 得来速）。
- **返回要点**：`data[]` 含 `storeCode`、`storeName`、`address`、`distance`（米）、`businessStatus`、`businessStartTime` / `businessEndTime`、`reservation`、`reservationTimeOptions`。
- **坑位**：
  - beType=1 时返回的门店**没有 beCode**，后续所有工具一律不传 beCode（传了会报错）；
  - `keyword` 接受两种粒度：区级关键词（「南山」→ 实测最近店 71m）和**地图复制的完整地址行**（「广东省深圳市南山区南山大道1175号新绿岛大厦」→ 实测最近店 32m；「南山区南山大道1175号新绿岛大厦」→ 13m）。含「省/市」前缀不出错，无需裁剪；
  - 返回结果已按距离升序，直接取前 N 家即可，不需要自己排序；
  - 门店的 `longitude` / `latitude` 字段实测返回 0.0（服务端内部地理编码，不回传坐标），距离以 `distance` 字段为准。
- **实测样本**（2026-10-12 单店快照，仅用于理解数据结构，**不可外推为普遍事实**）：`city=深圳市, keyword=南山, beType=1` → 5 家门店，71m / 438m / 539m / 711m / 827m。

### 2.2 query-store-coupons — 查门店真实可用券（唯一权威来源）

- **用途**：查询「指定门店 + 指定订单类型」下用户可使用的优惠券。
- **必填参数**：`storeCode`、`orderType`、`beType`（beCode 按矩阵）。
- **返回要点**：`title`（券名）、`couponId`、`couponCode`、`tradeDateTime`（有效期）、`products[]`（`productCode` + `productName`，券适用的商品）、`promotionId`。
- **坑位**：
  - **`query-my-coupons`（我的优惠券）不校验门店**，不能替代本工具——账户里有的券在这家店不一定能用；
  - 券的 `products[]` 商品**可能不在该店在售菜单里**（实测：某店返回「新人专享-中杯美式 7 折」，但其适用商品美式中杯不在该店 query-meals 菜单中）→ 必须与菜单交叉校验，不在售的标注「该券当前不可用」，不计入搭配；
  - **匹配铁律**：券商品与菜单的匹配必须按 `productCode` 精确匹配，**禁止按餐品名称人工放行**——实测「新人专享-中杯美式 7 折」的适用商品编码为 9900000119（美式中杯），不在该店菜单；而菜单里的美式是 507411（麦咖啡™美式），名称相近但编码不同。按名称放行就会向用户推荐一张根本用不了的券。combo_planner 已内置严格匹配，人工环节不得绕过；
  - 券面文字（如「7 折」「减 10 元」）**不等于最终优惠**——门槛、渠道、指定商品都会影响，必须用 calculate-price 带 `couponId` + `couponCode` 真跑一遍。

### 2.3 query-meals — 查门店在售菜单

- **用途**：查询门店可售餐品（分类、编码、价格、标签）。
- **必填参数**：`storeCode`、`orderType`、`beType`（beCode 按矩阵）；非预约场景不传 `reservationDate`。
- **返回要点**：
  - `categories[]`：分类名 + 该分类下餐品（`code` + `tags`）；
  - `meals{code}`：`name`、`currentPrice`（**元**字符串）、`originalPrice`、`discountType`、`canWithOrder`、`withOrder`。
- **坑位**：
  - **随单购陷阱**：`discountType="随单购麦金卡优惠"` 且 `canWithOrder=true` 的商品（如人气超值四件套 28 元）——该现价需随单购买麦金卡才享受，不买卡就是原价（57 元）。本技能默认**排除**此类商品，或在方案中明确标注「需随单购麦金卡」；
  - **周边混入菜单**：菜单含非餐品（联名棒球帽、毛绒周边、痒痒挠等），按名称关键词过滤（帽 / 毛绒 / 周边 / 挠 / 捶背 / 徽章 等），否则「件数最多」方案会推荐买帽子；
  - `tags` 是分类依据：「套餐」「随心拼」「小食拼盘」「麦金卡」「上新」「冰淇淋」等；
  - 同一餐品可能出现在多个分类，按 `code` 去重。
- **官方价格带搭配建议**（工具描述自带，可作为 combo-strategy 的依据）：
  - 20 元以下 → 小食；
  - 20–30 元 → 汉堡 + 小食 或 汉堡 + 饮料；
  - 30–40 元 → 汉堡 + 薯条/小食 + 饮料；
  - 40–50 元 → 汉堡 + 薯条 + 小食 + 饮料；
  - 50 元以上 → 丰富组合，优先不重复小食。
- **实测样本**（2026-10-12 单店快照，仅用于理解数据结构，**不可外推为普遍事实**）：单店菜单约 100 项，价格区间 3–72.5 元；随心配 1+1 为 13.9 / 14.9 元（2 件）；三件套 22.9–45.5 元；圆筒冰淇淋 / 灰焰圆筒 5 元。

### 2.4 calculate-price — 商品真实核价（技能核心）

- **用途**：对候选搭配逐一真实计算金额（含优惠、配送费、打包费）。**券面额 ≠ 真实优惠，一切以本接口返回为准。**
- **必填参数**：`storeCode`、`orderType`、`beType`（beCode 按矩阵）、`items[]`（每项 `productCode` + `quantity`；用券时同一 item 上传 `couponId` + `couponCode`）。
- **返回要点**：价格字段全部为「**分**」（如 `price=2880` → 28.80 元）；含 `deliveryPrice`（配送费）、`packingPrice`（打包费）、`discount`（优惠金额）、`productList`、`enjoyed` / `enjoyable`（券使用情况，`balance` 为门槛差额）。
- **坑位**：
  - 分 vs 元：展示时 ÷100，保留两位小数；
  - 配送费 / 打包费只在本接口可见——外送场景先用一个候选组合探测一次，读取 `deliveryPrice + packingPrice`，从预算中扣除后再枚举；
  - 返回金额超预算（如券门槛未达到）→ 丢弃该候选，换同策略的下一个候选；
  - 套餐子项选择用 `roundList`；特制选项 `modification` 的 key 规则：用户选中的项用 `selectedKey`，未选中但 `unselectedKey` 非空的也必须传 `unselectedKey`；
  - 调用预算：单店 ≤6 次（只核价 top 候选），控制总调用量防限流。

### 2.5 list-nutrition-foods — 餐品营养信息

- **用途**：能量、蛋白质、脂肪、碳水、钠、钙等；作为「饱腹度」三维度中的千卡参考。
- **用法**：会话内最多调 1 次建能量表，按餐品名称匹配求和；未覆盖的商品标注「营养数据暂缺」，**不得估算编造**。

### 2.6 now-time-info — 当前时间

- **用途**：涉及日期/有效期的判断（券是否过期、门店是否营业中）。
- **用法**：全程只调 1 次并缓存于会话；时间不靠模型记忆。

### 2.7 available-coupons — 麦麦省可领券列表

- **用途**：查询当前可领取的券（领券引导的素材来源）。
- **注意**：券「可领」≠ 在目标门店「可用」，领取后仍需 query-store-coupons 验证；引导用户领券时先展示列表并复述，得到确认后才考虑 auto-bind-coupons。

### 2.8 query-my-coupons — 我的优惠券

- **用途**：查看账户下已有券。
- **注意**：**不校验门店**，不能作为「本店可用券」展示；仅用于告知用户「你账户里已有这些券」。

## 3. 写操作工具（默认禁止，三重确认后才可调用）

| 工具 | 作用 | 调用前置条件 |
|---|---|---|
| `auto-bind-coupons` | 一键领取麦麦省所有可领券 | 用户明确说「领券」→ 先复述可领列表 → 用户确认 → 才调用；领后重查 query-store-coupons 更新方案 |
| `delivery-create-address` | 新增配送地址 | 用户明确说「保存这个地址」→ 复述地址六要素（城市/姓名/性别/电话/地址/门牌号）→ 用户确认 → 才调用 |
| `create-order` | 创建真实订单 | 用户明确说「下单/就要这个」→ 复述订单内容 + 金额 + 门店 → 用户确认 → 才调用；调用后如实返回支付链接，不代为支付 |
| `cancel-order` | 取消订单（补救） | 仅用于误建单后的补救：立即取消并向用户说明 |

## 4. 外送（麦乐送）链路专用工具

| 工具 | 用途 | 要点 |
|---|---|---|
| `delivery-query-addresses` | 查询用户配送地址簿 | 外送第一步；返回地址列表供用户选择 |
| `delivery-query-stores` | 查询地址附近可配送门店 | 门店结果含 `beCode`，后续工具按矩阵必传 |

## 5. 本技能不使用的工具（及原因）

| 工具 | 原因 |
|---|---|
| `mall-points-products` / `mall-product-detail` / `mall-create-order` / `mall-order-list` / `mall-order-detail` | 积分商城与兑换，与「预算内吃饱」无关且涉及真实积分扣减 |
| `query-lottery-info` / `draw-lottery` / `query-my-prizes` | 积分抽奖，无关 |
| `query-my-account` | 积分账户查询，无关 |
| `query-party-city` / `query-party-store` / `query-partystore-date` / `query-partystore-session` / `party-order-create` | 主题活动派对预订，场景不同 |
| `order-list` / `query-order` | 历史订单查询；本技能默认不建单，无需查询 |
| `campaign-calendar` | 活动日历，可作为后续增强，当前版本不用 |
| `query-meal-assistance` | 企业团餐助餐服务，默认场景不用 |
