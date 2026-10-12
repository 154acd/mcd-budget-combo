# MCP_INTEGRATION.md — mcd-budget-combo 的麦当劳 MCP 接入说明

> 本文档说明本项目实际使用的麦当劳 MCP Server、工具、调用流程与业务价值。所有「实测」数据均来自 2026-10-12 对官方 MCP 的真实调用记录，非模拟数据。

## 1. MCP Server 信息

| 项 | 值 |
| --- | --- |
| Server 名称 | mcd-mcp（麦当劳中国官方 MCP Server） |
| 接入地址 | https://mcp.mcd.cn |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | `Authorization: Bearer <MCP_TOKEN>` |
| Token 申请 | https://open.mcd.cn/mcp （手机号验证码登录 → 控制台 → 激活） |
| 限流 | 约 600 次/分钟/Token（据官方使用指南）；429 时退避 3 秒重试 1 次 |
| 官方仓库 | https://github.com/M-China/mcd-mcp-server |

本项目不内置、不传输、不存储任何 Token；配置文件仅使用环境变量占位符 `${MCD_MCP_TOKEN}`（见 mcp-config.example.json）。

## 2. 使用的工具清单（15 个）

### 2.1 只读查询工具（12 个）

| 工具 | 用途 | 关键参数 / 返回要点 |
| --- | --- | --- |
| `query-nearby-stores` | 到店场景按位置搜索附近门店 | 必填 searchType=2 + city + keyword + beType；返回 storeCode / distance（米，已按距离升序）/ businessStatus |
| `query-store-coupons` | 查「门店 + 订单类型」下真实可用券——**门店可用券的唯一权威来源** | 必填 storeCode + orderType + beType；返回 couponId / couponCode / products[]（适用商品编码） |
| `query-meals` | 查门店在售菜单 | 必填 storeCode + orderType + beType；返回 categories（分类+标签）与 meals{code→名称/现价/优惠类型}；**现价单位是「元」字符串** |
| `calculate-price` | 对候选搭配真实核价（含券、配送费、打包费） | items[] 传 productCode + quantity，用券时同项传 couponId + couponCode；**返回价格单位全部是「分」**，含 deliveryPrice / packingPrice / discount |
| `now-time-info` | 获取当前时间 | 全程仅调 1 次；券有效期与营业状态判断不靠模型记忆 |
| `available-coupons` | 麦麦省可领券列表 | 领券引导素材；「可领」≠ 门店可用，领取后仍需 query-store-coupons 验证 |
| `query-my-coupons` | 账户已有券 | 不校验门店，不得当作本店可用券展示 |
| `list-nutrition-foods` | 餐品营养（能量等） | 千卡参考来源；未覆盖商品标注「暂缺」，不编造 |
| `delivery-query-addresses` | 外送地址簿 | 外送第一步；无地址时引导用户提供六要素 |
| `delivery-query-stores` | 地址可配送门店 | 返回含 beCode，后续工具按矩阵必传 |
| `cancel-order` | 取消订单（仅补救） | 仅用于误建单后的补救，本技能默认不建单 |
| `query-meal-detail` | 餐品详情/套餐组成 | 需要展开套餐内部构成（如随心配两侧选什么）时按需调用 |

### 2.2 写操作工具（3 个，默认禁用，三重确认后才可调用）

| 工具 | 作用 | 调用前置条件 |
| --- | --- | --- |
| `auto-bind-coupons` | 一键领取麦麦省所有可领券 | 用户明确说「领券」→ 先复述可领列表 → 用户确认 → 才执行；领后重查 query-store-coupons 更新方案 |
| `create-order` | 创建真实订单 | 用户明确说「下单」→ 复述订单内容 + 金额 + 门店 → 用户确认 → 才执行 |
| `delivery-create-address` | 新增配送地址 | 用户明确说「保存这个地址」→ 复述地址六要素 → 用户确认 → 才执行 |

### 2.3 明确不使用的工具（及原因）

| 工具 | 原因 |
| --- | --- |
| `mall-points-products` / `mall-product-detail` / `mall-create-order` / `mall-order-list` / `mall-order-detail` | 积分商城与兑换，与「预算内吃饱」无关且涉及真实积分扣减 |
| `query-lottery-info` / `draw-lottery` / `query-my-prizes` | 积分抽奖，场景无关 |
| `query-my-account` | 积分账户查询，场景无关 |
| `query-party-city` / `query-party-store` / `query-partystore-date` / `query-partystore-session` / `party-order-create` | 主题活动派对预订，场景不同 |
| `order-list` / `query-order` | 历史订单查询；本技能默认不建单 |
| `campaign-calendar` | 活动日历，可作为后续增强，当前版本不用 |
| `query-meal-assistance` | 企业团餐助餐服务，默认场景不用 |

## 3. 调用流程

```
用户：「我在 <位置>，预算 <N> 元，怎么吃最饱？」
  │
  ① now-time-info ──────────── 全程 1 次，缓存复用
  │
  ② query-nearby-stores ────── city + keyword（支持地图复制的详细地址直贴）
  │                            → 按 distance 升序取前 5 家
  │
  ③ 逐店 query-store-coupons ── 门店真实可用券（唯一权威来源）
  │
  ④ 逐店 query-meals ────────── 菜单落盘 menu.json（单位：元）
  │
  ⑤ combo_planner.py plan ──── 本地零网络枚举 ≤6 个候选（0 次 MCP 调用）
  │                            （外送场景：先 calculate-price 探测配送费，扣除后再枚举）
  │
  ⑥ 逐候选 calculate-price ──── 真实核价（单位：分 → ÷100 展示）
  │                            超预算候选换下一个；用券传 couponId + couponCode
  │
  ⑦ 输出每家门店的搭配报告 ──── 最饱腹 / 件数最多 / 经典省心 / 用券多吃
```

**调用规模实测**：一次完整的三门店请求共 17 次 MCP 调用（now-time-info ×1 + 门店 ×1 + 券 ×3 + 菜单 ×3 + 核价 ×9），远低于限流阈值。

## 4. 真实调用记录（2026-10-12 实测）

### 4.1 门店搜索与「地址直贴」

`query-nearby-stores(city=深圳市, keyword=南山, searchType=2, beType=1)` 返回 5 家门店：

| storeCode | 门店 | 距离 |
| --- | --- | --- |
| 1420207 | 麦当劳深圳桂庙路餐厅 | 71m |
| 1420009 | 麦当劳深圳南山餐厅 | 438m |
| 1420876 | 麦当劳深圳鸿瑞花园餐厅 | 539m |
| 1420919 | 麦当劳深圳厚德品园餐厅 | 711m |
| 1420094 | 麦当劳深圳南山书城餐厅 | 827m |

**地图地址直贴验证**（本技能与 GPS/IP 定位方案的差异化设计）：

| keyword 输入 | 目标门店距离 | 排名 |
| --- | --- | --- |
| 「南山」 | 71m | 1 |
| 「南山区南山大道1175号新绿岛大厦」 | **13m** | 1 |
| 「广东省深圳市南山区南山大道1175号新绿岛大厦1层B号」（高德/百度复制格式，含省市前缀） | 32m | 1 |

结论：MCP 服务端内置地理编码，文字关键词即可获得 GPS 级距离排序，完整地址行原样直贴不出错——因此本技能**不引入 GPS 或 IP 定位依赖**。

### 4.2 券的「同名不同码」坑（本技能最核心的实测发现）

`query-store-coupons` 实测返回账户 2 张券（三家门店一致）：

| 券 | couponId | 适用商品编码 | 菜单实际情况 |
| --- | --- | --- | --- |
| 新人专享-中杯美式 7 折 | （已脱敏） | 9900000119（美式中杯） | **不在菜单**；菜单里的美式是 507411（麦咖啡™美式，11.9 元） |
| 新人专享-中杯奶铁 7 折 | （已脱敏） | 9900000117（奶铁中杯） | **不在菜单**；菜单里的奶铁是 507409（麦咖啡™奶铁，13.9 元） |

两张券面看起来都能用，但按 productCode 精确匹配后均判定「暂不可用」。**若按餐品名称人工放行，就会向用户推荐根本用不了的券。** combo_planner 的 `enum_coupon_driven` 内置严格匹配，实测三家门店 D 方案（用券多吃）均正确无产出。

### 4.3 菜单结构与门店差异

`query-meals` 单店实测 111–122 项餐品，价格区间 3–72.5 元（字符串、单位元）。实测发现的三个数据处理点：

1. **随单购陷阱**：四件套标价 28 元但 `discountType="随单购麦金卡优惠"`，不随单购买麦金卡实际按原价 57 元计——planner 默认排除此类商品；
2. **周边混入菜单**：联名棒球帽、毛绒周边、痒痒挠等非餐品在菜单中——planner 按关键词剔除；
3. **门店差异**：同区域 3 家门店菜单有实质差异（如「精选超值随心配」13.9 元仅 2/3 家门店在售）——逐店出方案可避免用户白跑。

### 4.4 真实核价

`calculate-price` 实测 9+ 次核价结果与菜单目录价分毫不差（如圆筒冰淇淋 ×2 → price=1000 分 = 10.00 元，discount=0，无隐藏费用）；返回结构含 deliveryPrice / packingPrice / discount / enjoyed / enjoyable（门槛差额），外送场景的配送费探测依赖该接口。

### 4.5 外送链路

`delivery-query-addresses` 实测该账户地址簿为空 → 按技能设计走「引导用户提供六要素」分支，未调用任何写工具。外送参数链（orderType=2 + beType=2 + beCode）按官方工具文档的矩阵实现，因账户无保存地址，配送费探针逻辑未做端到端实测（已在 README 已知限制中声明）。

### 4.6 可领券查询

`available-coupons` 实测返回 7 张可领券（中薯条买一送一、王牌小食拼、免费脆薯饼、人气麦旋风买一送一、9.9 元中杯冰美式等）——作为领券引导素材；**实测中未调用 auto-bind-coupons**（需用户明确确认后才执行）。

## 5. 业务价值

1. **消除「券面额 ≠ 真实优惠」的信息差**：实测账户 2/2 张券按名称看都可用、按编码实测都不可用。技能用 productCode 精确匹配 + calculate-price 真跑，把「看起来省钱」变成「真的省钱」。
2. **决策压缩**：人工「地图找店 → App 看菜单 → 翻券 → 心算价格 → 比门店」约 20–30 分钟；技能压缩为一轮对话、17 次 API 调用。
3. **门店差异显性化**：同区域门店菜单与券的差异逐店呈现，避免白跑。
4. **安全默认**：全程只读 + 写操作三重确认（明确指令 → 复述 → 用户确认），适合把账户操作交给 AI 的信任敏感场景。

## 6. 安全设计

- 不调用任何写工具，除非用户明确指令且完成复述确认；
- 仓库内无任何 Token / 密钥 / 账号信息；mcp-config.example.json 仅含 `${MCD_MCP_TOKEN}` 占位符；
- combo_planner.py 为纯标准库本地计算，零网络、零凭证，MCP 调用由 agent 按 SKILL.md 工作流执行，凭证处理代码不存在于本仓库；
- 价格数据一律来自官方接口实时返回，不缓存、不编造。
