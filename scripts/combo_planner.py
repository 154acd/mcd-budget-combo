#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
combo_planner.py — mcd-budget-combo 候选搭配枚举器

纯 Python 标准库实现：零网络、零 Token、零第三方依赖。
输入 query-meals 的菜单数据与预算，输出进入 calculate-price 核价的候选搭配。
最终价格一律以 agent 调用 calculate-price 的返回为准，本脚本只做目录价层面的筛选。

用法：
    python combo_planner.py plan --menu menu.json --budget 30 \
        [--coupons coupons.json] [--nutrition nutrition.json] [--max-candidates 3]
    python combo_planner.py selftest

输入文件格式见 references/combo-strategy.md 第 1.1 / 6 节。
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# ------------------------------------------------------------------ 常量

#: 同一商品最多买几件（避免「6 个甜筒」式失真推荐）
ITEM_QTY_CAP = 2
#: 小食 / 饮料 / 甜品各类的件数上限
CATEGORY_PIECE_CAP = {"小食": 3, "甜品": 3, "饮料": 3}

PERIPHERAL_KEYWORDS = ("帽", "毛绒", "周边", "挠", "捶背", "徽章", "玩具", "摆件")
STAPLE_EXTRA_KEYWORDS = ("麦香鸡", "麦香鱼")
SNACK_KEYWORDS = ("薯条", "薯饼", "薯", "鸡块", "鸡翅", "鸡排", "翅", "派", "玉米",
                  "苹果片", "芝士条", "扭扭薯", "心形薯饼", "脆汁鸡", "蘸酱炸鸡",
                  "麦乐鸡", "鸡")
DESSERT_KEYWORDS = ("冰淇淋", "圆筒", "新地", "麦旋风", "雪冰")
DRINK_KEYWORDS = ("可乐", "雪碧", "咖啡", "美式", "拿铁", "奶铁", "红茶", "绿茶",
                  "奶", "汁", "柠", "橙", "燕麦", "可可", "卡布奇诺", "阿芙佳朵",
                  "怡泉", "纯悦", "零度", "苏打")
CLASSIC_TAG_WORDS = ("经典", "爆款", "人气")
FILLER_CATEGORIES = ("小食", "饮料", "甜品")

CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# ------------------------------------------------------------------ 分类

def parse_pieces_from_name(name):
    """从名称中解析「N 件/翅/块」（支持阿拉伯数字与中文数字），无则返回 None。"""
    m = re.search(r"([0-9一二两三四五六七八九十]+)\s*(?:件|翅|块)", name)
    if not m:
        return None
    token = m.group(1)
    if token.isdigit():
        return int(token)
    if token in CN_NUM:
        return CN_NUM[token]
    if "十" in token:
        parts = token.split("十")
        tens = CN_NUM.get(parts[0], 1) if parts[0] else 1
        ones = CN_NUM.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        return tens * 10 + ones
    return None


def classify(name, price):
    """按名称关键词分类。返回 (类别, 件数, 主食数)。规则见 combo-strategy.md 1.2。"""
    if any(k in name for k in PERIPHERAL_KEYWORDS):
        return ("周边", 0, 0)
    if name.endswith("酱") and price < 5:
        return ("酱料", 0, 0)
    if "随心配" in name:
        return ("随心配", 2, 1)
    if any(k in name for k in ("双人餐", "亲子餐", "分享餐")):
        return ("多人餐", 4, 2)
    if "三件套" in name:
        return ("套餐", 3, 1)
    if "四件套" in name:
        return ("套餐", 4, 1)
    if "开心乐园餐" in name:
        return ("套餐", 2, 1)
    if "套餐" in name or "单人餐" in name:
        return ("套餐", parse_pieces_from_name(name) or 3, 1)
    if any(k in name for k in ("随心拼", "四宫格", "小食拼盘", "双拼")):
        return ("小食拼盘", parse_pieces_from_name(name) or 4, 0)
    if "堡" in name or "卷" in name:
        return ("主食", 1, 1)
    if any(k in name for k in STAPLE_EXTRA_KEYWORDS):
        return ("主食", 1, 1)
    if any(k in name for k in SNACK_KEYWORDS):
        return ("小食", parse_pieces_from_name(name) or 1, 0)
    if any(k in name for k in DESSERT_KEYWORDS):
        return ("甜品", 1, 0)
    if any(k in name for k in DRINK_KEYWORDS):
        return ("饮料", 1, 0)
    return ("其他", 1, 0)


# ------------------------------------------------------------------ 数据加载

def load_items(menu_path):
    """读取 query-meals 的返回（完整响应或 data 对象均可），展平为条目列表。"""
    with open(menu_path, encoding="utf-8") as f:
        raw = json.load(f)
    data = raw
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict) and "meals" not in raw:
        data = raw["data"]
    meals = data.get("meals") or {}

    tags_by_code, cat_by_code = {}, {}
    for cat in data.get("categories") or []:
        cname = (cat.get("name") or "").replace("\n", "")
        for m in cat.get("meals") or []:
            code = m.get("code")
            if code is None:
                continue
            tags_by_code.setdefault(str(code), set()).update(m.get("tags") or [])
            cat_by_code.setdefault(str(code), cname)

    items = []
    for code, m in meals.items():
        name = (m.get("name") or "").strip()
        try:
            price = float(str(m.get("currentPrice") or "0"))
        except (TypeError, ValueError):
            continue
        if not name or price <= 0:
            continue
        category, pieces, mains = classify(name, price)
        items.append({
            "code": str(code),
            "name": name,
            "price": round(price, 2),
            "category": category,
            "pieces": pieces,
            "mains": mains,
            "tags": sorted(tags_by_code.get(str(code), [])),
            "menu_category": cat_by_code.get(str(code), ""),
            "can_with_order": bool(m.get("canWithOrder")),
            "discount_type": m.get("discountType") or "",
        })
    return items


def prefilter(items, budget):
    """剔除周边/酱料/随单购商品与超预算单品。返回 (候选池, 剔除统计)。"""
    pool, excluded = [], {"peripheral": 0, "sauce": 0, "with_order": 0, "over_budget": 0}
    for it in items:
        if it["category"] == "周边":
            excluded["peripheral"] += 1
            continue
        if it["category"] == "酱料":
            excluded["sauce"] += 1
            continue
        if it["can_with_order"] or "随单购" in it["discount_type"]:
            excluded["with_order"] += 1
            continue
        if it["price"] > budget + 1e-9:
            excluded["over_budget"] += 1
            continue
        pool.append(it)
    return pool, excluded


# ------------------------------------------------------------------ 候选构建

def _sums(combo, by_code):
    total = sum(by_code[c]["price"] * q for c, q in combo.items())
    pieces = sum(by_code[c]["pieces"] * q for c, q in combo.items())
    mains = sum(by_code[c]["mains"] * q for c, q in combo.items())
    return round(total, 2), pieces, mains


def build_candidate(strategy, combo, by_code, nutrition=None, coupon=None):
    total, pieces, mains = _sums(combo, by_code)
    items = [
        {"code": c, "name": by_code[c]["name"], "price": by_code[c]["price"], "quantity": q}
        for c, q in sorted(combo.items(), key=lambda kv: (-by_code[kv[0]]["price"], kv[0]))
    ]
    kcal, note = None, "无营养数据"
    if nutrition:
        matched = sum(1 for c in combo if by_code[c]["name"] in nutrition)
        if matched:
            kcal = round(sum(nutrition.get(by_code[c]["name"], 0) * q for c, q in combo.items()))
            note = "%d/%d 项有数据" % (matched, len(combo))
        else:
            note = "营养数据暂缺"
    return {
        "strategy": strategy,
        "items": items,
        "catalog_total": total,
        "pieces": pieces,
        "mains": mains,
        "kcal": kcal,
        "kcal_note": note,
        "coupon": coupon,
    }


# ------------------------------------------------------------------ 四种枚举模板

def enum_max_satiety(pool, budget, nutrition=None):
    """最饱腹：1 个预算内主食锚点（主食数降序→件数降序→价格升序），剩余额度补小食/饮料/甜品。"""
    by_code = {it["code"]: it for it in pool}
    anchors = [it for it in pool if it["mains"] >= 1 and it["price"] <= budget + 1e-9]
    if not anchors:
        return None
    anchors.sort(key=lambda it: (-it["mains"], -it["pieces"], it["price"], it["code"]))
    anchor = anchors[0]
    combo, total = {anchor["code"]: 1}, anchor["price"]
    fillers = [it for it in pool
               if it["code"] != anchor["code"] and it["category"] in FILLER_CATEGORIES]
    fillers.sort(key=lambda it: (-(it["pieces"] / it["price"]), it["price"], it["code"]))
    for f in fillers:
        if total + f["price"] <= budget + 1e-9:
            combo[f["code"]] = 1
            total += f["price"]
    return build_candidate("max_satiety", combo, by_code, nutrition=nutrition)


def enum_max_pieces(pool, budget, nutrition=None):
    """件数最多：按件数/元性价比贪心；同商品最多 2 件，小食/饮料/甜品各类最多 3 件。"""
    by_code = {it["code"]: it for it in pool}
    combo, total, cat_pieces = {}, 0.0, {}
    while True:
        best = None
        for it in pool:
            if combo.get(it["code"], 0) >= ITEM_QTY_CAP:
                continue
            cap = CATEGORY_PIECE_CAP.get(it["category"])
            if cap is not None and cat_pieces.get(it["category"], 0) + it["pieces"] > cap:
                continue
            if total + it["price"] > budget + 1e-9:
                continue
            key = (-(it["pieces"] / it["price"]), it["price"], it["code"])
            if best is None or key < best[0]:
                best = (key, it)
        if best is None:
            break
        it = best[1]
        combo[it["code"]] = combo.get(it["code"], 0) + 1
        total += it["price"]
        cat_pieces[it["category"]] = cat_pieces.get(it["category"], 0) + it["pieces"]
    if not combo:
        return None
    return build_candidate("max_pieces", combo, by_code, nutrition=nutrition)


def enum_classic(pool, budget, nutrition=None):
    """经典省心：优先带经典/爆款/人气标识的套餐或随心配，单套输出。"""
    by_code = {it["code"]: it for it in pool}
    cands = [it for it in pool if it["price"] <= budget + 1e-9]
    if not cands:
        return None

    def sort_key(it):
        has_word = any(w in it["name"] or any(w in t for t in it["tags"])
                       for w in CLASSIC_TAG_WORDS)
        is_set = it["category"] in ("套餐", "随心配", "多人餐")
        group = 0 if (has_word and is_set) else (1 if is_set else 2)
        return (group, -it["pieces"], it["price"], it["code"])

    cands.sort(key=sort_key)
    best = cands[0]
    return build_candidate("classic", {best["code"]: 1}, by_code, nutrition=nutrition)


def enum_coupon_driven(pool, budget, coupons, nutrition=None):
    """用券多吃：仅当券的全部适用商品都在菜单且目录价合计 ≤ 预算时才产出。"""
    by_code = {it["code"]: it for it in pool}
    out = []
    for c in coupons or []:
        codes = [str(p.get("productCode")) for p in (c.get("products") or [])
                 if p.get("productCode")]
        if not codes or any(cd not in by_code for cd in codes):
            continue  # 有商品不在菜单 → 该券当前不可用，跳过
        base = sum(by_code[cd]["price"] for cd in codes)
        if base > budget + 1e-9:
            continue
        combo, total = {cd: 1 for cd in codes}, base
        fillers = [it for it in pool if it["code"] not in combo
                   and it["category"] in FILLER_CATEGORIES]
        fillers.sort(key=lambda it: (-(it["pieces"] / it["price"]), it["price"], it["code"]))
        for f in fillers:
            if total + f["price"] <= budget + 1e-9:
                combo[f["code"]] = 1
                total += f["price"]
        out.append(build_candidate(
            "coupon_driven", combo, by_code, nutrition=nutrition,
            coupon={"title": c.get("title"), "couponId": c.get("couponId"),
                    "couponCode": c.get("couponCode")}))
    return out


def dedup_and_sort(cands):
    """按 (code, quantity) 集合去重，按 主食数降序→件数降序→目录价升序 排序。"""
    seen, out = set(), []
    for c in sorted(cands, key=lambda c: (-c["mains"], -c["pieces"], c["catalog_total"])):
        key = frozenset((i["code"], i["quantity"]) for i in c["items"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# ------------------------------------------------------------------ 子命令

def cmd_plan(args):
    if args.budget <= 0:
        print("错误：预算必须大于 0", file=sys.stderr)
        return 2
    items = load_items(args.menu)
    if not items:
        print(json.dumps({"budget": args.budget, "candidates": [],
                          "notes": {"error": "菜单为空或无法解析"}},
                         ensure_ascii=False, indent=2))
        return 1
    pool, excluded = prefilter(items, args.budget)
    coupons = []
    if args.coupons:
        with open(args.coupons, encoding="utf-8") as f:
            coupons = json.load(f)
    else:
        # 券内联：读取 menu 文件顶层 coupons 键（与 mcd-bulk-combo 一致的内联通路）
        try:
            with open(args.menu, encoding="utf-8") as f:
                raw_menu = json.load(f)
            if isinstance(raw_menu, dict):
                coupons = raw_menu.get("coupons") or []
        except (OSError, json.JSONDecodeError):
            coupons = []
    nutrition = None
    if args.nutrition:
        with open(args.nutrition, encoding="utf-8") as f:
            nutrition = json.load(f)

    cands = []
    for fn in (enum_max_satiety, enum_max_pieces, enum_classic):
        c = fn(pool, args.budget, nutrition)
        if c:
            cands.append(c)
    cands.extend(enum_coupon_driven(pool, args.budget, coupons, nutrition))
    cands = dedup_and_sort(cands)[: args.max_candidates]

    result = {
        "budget": args.budget,
        "candidates": cands,
        "notes": {
            "menu_items": len(items),
            "pool_size": len(pool),
            "excluded": excluded,
            "unit": "catalog_total 为目录价（元）；最终价格以 calculate-price 实时核价为准",
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


# ------------------------------------------------------------------ 自检

SELFTEST_MENU = {
    "categories": [
        {"name": "随心配1+1", "meals": [{"code": "A1", "tags": ["超值百种组合随心配"]}]},
        {"name": "鸡肉汉堡", "meals": [{"code": "B1", "tags": ["单品"]}]},
        {"name": "套餐", "meals": [{"code": "C1", "tags": ["套餐"]},
                                   {"code": "C2", "tags": ["套餐", "麦金卡"]}]},
        {"name": "小食甜品", "meals": [{"code": "D1", "tags": ["小食"]},
                                       {"code": "E1", "tags": ["冰淇淋"]},
                                       {"code": "E2", "tags": ["冰淇淋"]},
                                       {"code": "F1", "tags": []},
                                       {"code": "G1", "tags": []}]},
    ],
    "meals": {
        "A1": {"name": "人气经典随心配", "currentPrice": "14.9", "canWithOrder": False},
        "B1": {"name": "麦辣鸡腿汉堡", "currentPrice": "22.5", "canWithOrder": False},
        "C1": {"name": "麦香鸡套餐", "currentPrice": "28", "canWithOrder": False},
        "C2": {"name": "板烧鸡腿堡四件套", "currentPrice": "28", "canWithOrder": True,
               "discountType": "随单购麦金卡优惠"},
        "D1": {"name": "苹果片", "currentPrice": "7", "canWithOrder": False},
        "E1": {"name": "圆筒冰淇淋", "currentPrice": "5", "canWithOrder": False},
        "E2": {"name": "灰焰圆筒", "currentPrice": "5", "canWithOrder": False},
        "F1": {"name": "麦当劳联名复古棒球帽", "currentPrice": "34.9", "canWithOrder": True,
               "discountType": "随单购麦金卡优惠"},
        "G1": {"name": "韩式烟熏芝士风味酱", "currentPrice": "3", "canWithOrder": False},
    },
}

SELFTEST_COUPONS = [
    {"title": "测试可用券", "couponId": "C1", "couponCode": "c1",
     "products": [{"productCode": "A1", "productName": "人气经典随心配"}]},
    {"title": "测试失效券", "couponId": "C2", "couponCode": "c2",
     "products": [{"productCode": "X99", "productName": "不存在的商品"}]},
]


def _plan_from_menu(menu, budget, coupons=None, nutrition=None):
    """内存版 plan（供自检复用，不落盘）。"""
    import tempfile
    import os
    import io
    import contextlib

    def _dump(obj):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                        encoding="utf-8")
        json.dump(obj, f, ensure_ascii=False)
        f.close()
        return f.name

    menu_path = _dump(menu)
    paths = [menu_path]
    coupon_path = _dump(coupons) if coupons else None
    nutrition_path = _dump(nutrition) if nutrition else None
    if coupon_path:
        paths.append(coupon_path)
    if nutrition_path:
        paths.append(nutrition_path)
    try:
        args = argparse.Namespace(menu=menu_path, budget=budget,
                                  coupons=coupon_path, nutrition=nutrition_path,
                                  max_candidates=6)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_plan(args)
        return json.loads(buf.getvalue())
    finally:
        for p in paths:
            os.unlink(p)


def cmd_selftest(args=None):
    failures = []

    def check(name, cond, detail=""):
        if cond:
            print("  [PASS] %s" % name)
        else:
            print("  [FAIL] %s %s" % (name, detail))
            failures.append(name)

    print("combo_planner selftest")

    # 1. 名称件数解析
    check("parse_pieces: 阿拉伯数字", parse_pieces_from_name("堡堡随心拼（7件任选）") == 7)
    check("parse_pieces: 中文数字", parse_pieces_from_name("黄金十翅桶") == 10)
    check("parse_pieces: 无件数", parse_pieces_from_name("麦辣鸡腿汉堡") is None)

    # 2. 分类
    check("classify: 随心配=2件1主食", classify("人气经典随心配", 14.9) == ("随心配", 2, 1))
    check("classify: 四件套=4件", classify("板烧鸡腿堡四件套", 28) == ("套餐", 4, 1))
    check("classify: 周边剔除", classify("麦当劳联名复古棒球帽", 34.9)[0] == "周边")
    check("classify: 酱料剔除", classify("韩式烟熏芝士风味酱", 3)[0] == "酱料")

    # 3. 预算 30 全流程
    r30 = _plan_from_menu(SELFTEST_MENU, 30, coupons=SELFTEST_COUPONS)
    ex = r30["notes"]["excluded"]
    check("预过滤: 周边/酱料/随单购均被剔除",
          ex["peripheral"] == 1 and ex["sauce"] == 1 and ex["with_order"] == 1,
          str(ex))
    check("候选池=6", r30["notes"]["pool_size"] == 6, str(r30["notes"]["pool_size"]))
    strategies = [c["strategy"] for c in r30["candidates"]]
    check("四模板均有输出（有可用券时）",
          set(strategies) >= {"max_satiety", "max_pieces", "classic", "coupon_driven"},
          str(strategies))
    check("所有候选不超预算",
          all(c["catalog_total"] <= 30 + 1e-9 for c in r30["candidates"]))
    mp = next(c for c in r30["candidates"] if c["strategy"] == "max_pieces")
    check("max_pieces 件数>=4 且不超预算", mp["pieces"] >= 4 and mp["catalog_total"] <= 30,
          "%s 件 / %s 元" % (mp["pieces"], mp["catalog_total"]))
    check("max_pieces 同商品不超 2 件",
          all(i["quantity"] <= ITEM_QTY_CAP for i in mp["items"]))
    ms = next(c for c in r30["candidates"] if c["strategy"] == "max_satiety")
    check("max_satiety 含主食锚点且件数>=3", ms["mains"] >= 1 and ms["pieces"] >= 3,
          "%s 件 / %s 主食" % (ms["pieces"], ms["mains"]))
    cl = next(c for c in r30["candidates"] if c["strategy"] == "classic")
    check("classic 选经典标识套餐", cl["items"][0]["name"] == "人气经典随心配",
          cl["items"][0]["name"])
    cd = [c for c in r30["candidates"] if c["strategy"] == "coupon_driven"]
    check("coupon_driven 只产出在售券组合", len(cd) == 1
          and cd[0]["coupon"]["title"] == "测试可用券", str(len(cd)))

    # 4. 预算 10 边界
    r10 = _plan_from_menu(SELFTEST_MENU, 10)
    check("低预算: 候选非空", len(r10["candidates"]) > 0)
    check("低预算: 全部不超 10 元",
          all(c["catalog_total"] <= 10 + 1e-9 for c in r10["candidates"]))
    check("低预算: 不包含超预算单品",
          all(i["code"] not in ("A1", "B1", "C1") for c in r10["candidates"] for i in c["items"]))

    # 5. 无券时 coupon_driven 不产出
    r_nc = _plan_from_menu(SELFTEST_MENU, 30)
    check("无券: coupon_driven 不产出",
          all(c["strategy"] != "coupon_driven" for c in r_nc["candidates"]))

    # 6. 营养参考
    r_nu = _plan_from_menu(SELFTEST_MENU, 30,
                           nutrition={"圆筒冰淇淋": 150, "灰焰圆筒": 160})
    mp2 = next(c for c in r_nu["candidates"] if c["strategy"] == "max_pieces")
    check("千卡: 仅累计有数据项并标注",
          mp2["kcal"] is not None and "项有数据" in mp2["kcal_note"],
          "%s / %s" % (mp2["kcal"], mp2["kcal_note"]))

    print()
    if failures:
        print("selftest 失败 %d 项：%s" % (len(failures), "、".join(failures)))
        return 1
    print("selftest 全部通过")
    return 0


# ------------------------------------------------------------------ 入口

def main(argv=None):
    parser = argparse.ArgumentParser(description="mcd-budget-combo 候选搭配枚举器")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="生成候选搭配")
    p_plan.add_argument("--menu", required=True, help="query-meals 返回的 data 对象（JSON 文件）")
    p_plan.add_argument("--budget", required=True, type=float, help="预算（元）")
    p_plan.add_argument("--coupons", help="可选：query-store-coupons 返回的券数组（JSON 文件）")
    p_plan.add_argument("--nutrition", help="可选：list-nutrition-foods 的 {名称: 千卡} 映射（JSON 文件）")
    p_plan.add_argument("--max-candidates", type=int, default=3, help="进入核价的候选上限，默认 3（1 分钟口径）")
    p_plan.set_defaults(func=cmd_plan)

    p_test = sub.add_parser("selftest", help="内置断言自检（无需输入文件）")
    p_test.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
