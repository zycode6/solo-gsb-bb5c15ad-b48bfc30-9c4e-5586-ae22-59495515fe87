"""plan_monitors 失效监测点布设预览测试: 可分性 / 费用与字典序决胜 /
不可区分情形对 / 不关阀方案 / 录入校验 / 随机对拍独立暴力枚举。"""
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from solver import (  # noqa: E402
    MAX_MONITOR_CANDIDATES, NetworkValidationError, plan_monitors, solve,
    solve_failover, validate_network,
)

passed = 0


def check(cond, msg):
    global passed
    assert cond, "FAIL: " + msg
    passed += 1


def expect_errors(snapshot, closed, cands, keys):
    try:
        plan_monitors(snapshot, closed, cands)
    except NetworkValidationError as exc:
        joined = " | ".join(exc.errors)
        for key in keys:
            check(key in joined, f"error contains {key!r}; got: {exc.errors}")
        return
    raise AssertionError("should have raised: " + str(keys))


# ---- 双漏点分支: 阀在"进水点-分支"边上, 各阀失效只恢复自己分支 ----
# S1 -V1(e1)- M1, M1-X(无阀) / M1-L1(无阀); S2 -V2(e4)- M2, M2-Y / M2-L2;
# S1-H(必保, 无阀)
NET = {
    "nodes": [
        {"id": "S1", "role": "inlet"}, {"id": "S2", "role": "inlet"},
        {"id": "M1", "role": "normal"}, {"id": "M2", "role": "normal"},
        {"id": "X", "role": "normal"}, {"id": "Y", "role": "normal"},
        {"id": "L1", "role": "leak"}, {"id": "L2", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "e1", "endpoint_a": "S1", "endpoint_b": "M1"},
        {"id": "e2", "endpoint_a": "M1", "endpoint_b": "X"},
        {"id": "e3", "endpoint_a": "M1", "endpoint_b": "L1"},
        {"id": "e4", "endpoint_a": "S2", "endpoint_b": "M2"},
        {"id": "e5", "endpoint_a": "M2", "endpoint_b": "Y"},
        {"id": "e6", "endpoint_a": "M2", "endpoint_b": "L2"},
        {"id": "e7", "endpoint_a": "S1", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "e1", "cost": 1},
        {"id": "V2", "segment_id": "e4", "cost": 1},
    ],
}
SOL = solve(NET)
check(SOL["feasible"] and SOL["closed_valves"] == ["V1", "V2"],
      f"base plan {SOL.get('closed_valves')}")

# 1. 基本求解: X/Y 各 2/3, 同签名的 L1/M1 贵, H 恒定 -> 选 X,Y, 费用 5
r = plan_monitors(NET, SOL["closed_valves"], [
    {"node_id": "X", "fee": 2},
    {"node_id": "Y", "fee": 3},
    {"node_id": "M1", "fee": 10},
    {"node_id": "L1", "fee": 10},
    {"node_id": "H", "fee": 1},
])
check(r["ok"] and r["distinguishable"], "net1 distinguishable")
check(r["selected_ids"] == ["X", "Y"], f"net1 selected {r['selected_ids']}")
check(r["place_cost_total"] == 5, "net1 cost")
check(r["scenario_count"] == 3 and len(r["scenarios"]) == 3, "net1 scenarios")
check(r["candidate_count"] == 5 and r["subset_count"] == 32, "net1 comb")
check(r["indistinguishable_pairs"] == [], "net1 no pairs")
check([p["node_id"] for p in r["selected_points"]] == ["X", "Y"],
      "net1 selected_points order")
check(all(p["role"] == "normal" for p in r["selected_points"]),
      "net1 point roles")
# 情形 key / 标签 / 读数
keys = [sc["key"] for sc in r["scenarios"]]
check(keys == ["normal", "valve:V1", "valve:V2"], f"net1 keys {keys}")
norm = r["scenarios"][0]
check(norm["failed_valve"] is None and norm["segment_id"] is None, "normal meta")
check(r["scenarios"][1]["failed_valve"] == "V1"
      and r["scenarios"][1]["segment_id"] == "e1", "v1 meta")
# 读数: 正常全干(X,Y); V1 失效 X 有水 Y 无水; V2 失效反之
def wet_map(sc):
    return {x["node_id"]: x["wet"] for x in sc["readings"]}

check(not wet_map(norm)["X"] and not wet_map(norm)["Y"], "normal X/Y dry")
check(wet_map(r["scenarios"][1])["X"] and not wet_map(r["scenarios"][1])["Y"],
      "V1 fail X wet Y dry")
check(not wet_map(r["scenarios"][2])["X"] and wet_map(r["scenarios"][2])["Y"],
      "V2 fail X dry Y wet")
# 选中点读数三行两两不同
vectors = [tuple(x["wet"] for x in sc["readings"]
                 if x["node_id"] in ("X", "Y")) for sc in r["scenarios"]]
check(len(set(vectors)) == 3, f"net1 vectors {vectors}")
check("不修改" in r["message"], "net1 readonly note")

# 2. 同费字典序决胜: 同签名候选 (X,M1,L1) 与 (Y,M2,L2) 全部费用 1
#    -> L1,L2 (编号升序列表字典序最小)
r = plan_monitors(NET, SOL["closed_valves"], [
    {"node_id": "X", "fee": 1}, {"node_id": "Y", "fee": 1},
    {"node_id": "M1", "fee": 1}, {"node_id": "M2", "fee": 1},
    {"node_id": "L1", "fee": 1}, {"node_id": "L2", "fee": 1},
])
check(r["distinguishable"] and r["selected_ids"] == ["L1", "L2"],
      f"lex selected {r['selected_ids']}")

# 2b. 字符串字典序: L10 < L2
NET_STR = {
    "nodes": [n for n in NET["nodes"] if n["id"] not in ("L1", "L2")] + [
        {"id": "L10", "role": "leak"}, {"id": "L2", "role": "leak"}],
    "segments": [s for s in NET["segments"] if s["id"] not in ("e3", "e6")] + [
        {"id": "e8", "endpoint_a": "M1", "endpoint_b": "L10"},
        {"id": "e9", "endpoint_a": "M2", "endpoint_b": "L2"}],
    "valves": NET["valves"],
}
r = plan_monitors(NET_STR, SOL["closed_valves"], [
    {"node_id": "L10", "fee": 1}, {"node_id": "L2", "fee": 1},
    {"node_id": "X", "fee": 5}, {"node_id": "Y", "fee": 5},
])
check(r["selected_ids"] == ["L10", "L2"], f"str lex {r['selected_ids']}")

# 3. 费用优先于字典序: 便宜的 X,Y(费用 2) 胜过 L1,L2(费用 20)
r = plan_monitors(NET, SOL["closed_valves"], [
    {"node_id": "X", "fee": 1}, {"node_id": "Y", "fee": 1},
    {"node_id": "L1", "fee": 10}, {"node_id": "L2", "fee": 10},
])
check(r["selected_ids"] == ["X", "Y"] and r["place_cost_total"] == 2,
      "cost first")

# 4. 候选无法区分: 只给同签名候选(X/M1/L1) -> 正常 与 V2 失效不可分
r = plan_monitors(NET, SOL["closed_valves"], [
    {"node_id": "X", "fee": 1},
    {"node_id": "M1", "fee": 1},
    {"node_id": "L1", "fee": 1},
])
check(not r["distinguishable"], "net4 not distinguishable")
check(r["selected_ids"] == [] and r["place_cost_total"] == 0, "net4 empty sel")
pairs = [(p["scenarios"][0]["key"], p["scenarios"][1]["key"])
         for p in r["indistinguishable_pairs"]]
check(pairs == [("normal", "valve:V2")], f"net4 pairs {pairs}")
# 情形对含完整标签与阀门信息
p0 = r["indistinguishable_pairs"][0]["scenarios"]
check(p0[0]["failed_valve"] is None and p0[1]["failed_valve"] == "V2",
      "net4 pair meta")
# 不可区分时仍返回全部候选点的各情形读数
check(all(len(sc["readings"]) == 3 for sc in r["scenarios"]),
      "net4 readings cover candidates")
check("无法区分" in r["message"], "net4 message")

# 4b. 无候选: 三对情形全部不可分
r = plan_monitors(NET, SOL["closed_valves"], [])
check(not r["distinguishable"]
      and len(r["indistinguishable_pairs"]) == 3, "no candidate pairs")
check(r["subset_count"] == 1 and r["candidate_count"] == 0, "no candidate meta")

# 5. 不关闭任何阀门的可行方案: 只有一种情形, 空点集费用 0 即可区分
NET_NO_VALVE = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "A", "role": "normal"},
    ],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "A"}],
    "valves": [],
}
S0 = solve(NET_NO_VALVE)
check(S0["feasible"] and S0["closed_valves"] == [], "no-valve plan")
r = plan_monitors(NET_NO_VALVE, [], [{"node_id": "A", "fee": 9}])
check(r["distinguishable"] and r["selected_ids"] == []
      and r["place_cost_total"] == 0 and r["scenario_count"] == 1,
      "no-valve preview")
check(len(r["scenarios"]) == 1 and r["scenarios"][0]["key"] == "normal",
      "no-valve only normal")

# 6. 漏点/必保点也可作为监测点(非进水即可), 读数随情形变化
r = plan_monitors(NET, SOL["closed_valves"], [
    {"node_id": "L1", "fee": 1}, {"node_id": "L2", "fee": 1},
    {"node_id": "H", "fee": 1},
])
check(r["distinguishable"] and r["selected_ids"] == ["L1", "L2"],
      f"leak points {r['selected_ids']}")
check({p["role"] for p in r["selected_points"]} == {"leak"}, "leak roles")

# ---- 7. 录入校验 ----
# 候选超过 12 个
expect_errors(NET, SOL["closed_valves"], [
    {"node_id": f"N{i}", "fee": 1} for i in range(MAX_MONITOR_CANDIDATES + 1)],
    ["候选监测点数量", f"超过上限 {MAX_MONITOR_CANDIDATES}"])
# 编号重复
expect_errors(NET, SOL["closed_valves"], [
    {"node_id": "X", "fee": 1}, {"node_id": "X", "fee": 2}],
    ["候选监测点编号重复", "X"])
# 节点不存在
expect_errors(NET, SOL["closed_valves"], [
    {"node_id": "ZZ", "fee": 1}], ["节点 ZZ 不存在"])
# 进水点不能布设
expect_errors(NET, SOL["closed_valves"], [
    {"node_id": "S1", "fee": 1}], ["S1 是进水点"])
# 缺少节点编号
expect_errors(NET, SOL["closed_valves"], [{"fee": 1}], ["缺少节点编号"])
# 布设费非法: 0 / 负 / 小数 / 字符串 / bool / null / 缺失
for bad in [0, -3, 1.5, "5", "x", True, None]:
    expect_errors(NET, SOL["closed_valves"], [
        {"node_id": "X", "fee": bad}], ["布设费必须是正整数"])
expect_errors(NET, SOL["closed_valves"], [{"node_id": "X"}], ["布设费必须是正整数"])
# candidates 不是数组
expect_errors(NET, SOL["closed_valves"], {"node_id": "X"},
              ["candidates 必须是数组"])
# 候选不是对象
expect_errors(NET, SOL["closed_valves"], ["X"], ["必须是对象"])
# 方案关闭的阀门不在快照中
expect_errors(NET, ["V9"], [], ["阀门 V9 不存在于方案快照"])

# ---- 8. 随机对拍: 与独立暴力枚举比较 (费用, 编号列表) 与不可区分对 ----
rng = random.Random(20260925)
trials = 0
for trial in range(900):
    n = rng.randint(3, 9)
    node_ids = [f"n{i}" for i in range(n)]
    roles = ["inlet", "leak"] + ["normal"] * (n - 2)
    rng.shuffle(roles)
    role_map = dict(zip(node_ids, roles))
    # 随机连通图(生成树 + 随机加边)
    order = node_ids[:]
    rng.shuffle(order)
    seg_list, used, si = [], [order[0]], 0
    for u in order[1:]:
        v = rng.choice(used)
        seg_list.append({"id": f"e{si}", "endpoint_a": u, "endpoint_b": v})
        used.append(u); si += 1
    for _ in range(rng.randint(0, 3)):
        a, b = rng.sample(node_ids, 2)
        seg_list.append({"id": f"e{si}", "endpoint_a": a, "endpoint_b": b})
        si += 1
    for nid in node_ids:
        if role_map[nid] == "normal" and rng.random() < 0.25:
            role_map[nid] = "must_keep"
    k = rng.randint(0, min(5, len(seg_list)))
    chosen = rng.sample(seg_list, k) if k else []
    valve_list = [{"id": f"V{i+1}", "segment_id": sg["id"],
                   "cost": rng.randint(1, 9)}
                  for i, sg in enumerate(chosen)]
    net = {"nodes": [{"id": nid, "role": role_map[nid]} for nid in node_ids],
           "segments": seg_list, "valves": valve_list}
    sol = solve(net)
    if not sol["feasible"]:
        continue
    plan = sol["closed_valves"]
    # 随机候选: 0..6 个非进水节点(允许重复, 重复时求解器应报错 -> 去重)
    non_inlet = [nid for nid in node_ids if role_map[nid] != "inlet"]
    m = rng.randint(0, min(6, len(non_inlet)))
    cand_ids = rng.sample(non_inlet, m) if m else []
    cand_list = [{"node_id": nid, "fee": rng.randint(1, 9)}
                 for nid in cand_ids]
    res = plan_monitors(net, plan, cand_list)

    # 独立暴力: 用简化清晰的 BFS 重新实现一遍
    nodes2, segments2, valves2 = validate_network(net)
    idx2 = {nd["id"]: nd for nd in nodes2}
    adj0 = {nid: [] for nid in node_ids}
    vseg = {vl["id"]: vl["segment_id"] for vl in valves2}
    for sg in segments2:
        adj0[sg["endpoint_a"]].append((sg["endpoint_b"], sg["valve_id"]))
        adj0[sg["endpoint_b"]].append((sg["endpoint_a"], sg["valve_id"]))
    inlet_ids = {nid for nid in node_ids if role_map[nid] == "inlet"}
    closed_seg = {vseg[v] for v in plan}

    def reach(extra_valve):
        open_seg = vseg.get(extra_valve)
        blocked = closed_seg - ({open_seg} if open_seg else set())
        seen, stack = set(inlet_ids), list(inlet_ids)
        while stack:
            u = stack.pop()
            for w, vid in adj0[u]:
                if vid is not None and vseg[vid] in blocked:
                    continue
                if w not in seen:
                    seen.add(w); stack.append(w)
        return seen

    sc_names = [None] + plan
    reaces = [reach(v) for v in sc_names]
    t = len(sc_names)
    wets = {c["node_id"]: [c["node_id"] in f for f in reaces]
            for c in cand_list}
    bf_pairs = [(a, b) for a in range(t) for b in range(a + 1, t)
                if all(wets[c["node_id"]][a] == wets[c["node_id"]][b]
                       for c in cand_list)]

    if bf_pairs:
        check(not res["distinguishable"], f"trial {trial}: should be inseparable")
        # key -> failed_valve(None 表示正常), 映射回下标
        def key_index(k):
            if k == "normal":
                return 0
            return sc_names.index(k.split(":", 1)[1])
        got_pairs = sorted((key_index(p["scenarios"][0]["key"]),
                            key_index(p["scenarios"][1]["key"]))
                           for p in res["indistinguishable_pairs"])
        check(got_pairs == sorted(bf_pairs),
              f"trial {trial}: pairs {got_pairs} != {bf_pairs}")
    else:
        check(res["distinguishable"], f"trial {trial}: should be separable")
        best = None
        for s in range(1 << m):
            sel = [cand_list[j]["node_id"] for j in range(m) if (s >> j) & 1]
            vecs = {tuple(wets[nid][a] for nid in sel) for a in range(t)}
            if len(vecs) < t:
                continue
            fee = sum(cand_list[j]["fee"] for j in range(m) if (s >> j) & 1)
            key = (fee, tuple(sorted(sel)))
            if best is None or key < best:
                best = key
        got = (res["place_cost_total"], tuple(res["selected_ids"]))
        check(got == best, f"trial {trial}: {got} != brute {best}")
        # 自洽: 费用合计一致; 选中点读数两两不同
        check(sum(p["fee"] for p in res["selected_points"])
              == res["place_cost_total"], f"trial {trial} fee sum")
        sel_set = set(res["selected_ids"])
        vecs = {tuple(x["wet"] for x in sc["readings"]
                      if x["node_id"] in sel_set)
                for sc in res["scenarios"]}
        check(len(vecs) == t, f"trial {trial} selected vectors unique")
    trials += 1
check(trials >= 200, f"enough random trials ({trials})")

# ---- 9. 备援方案快照同样适用 ----
NET_FO = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "M", "role": "normal"},
        {"id": "X", "role": "normal"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "M"},
        {"id": "p2", "endpoint_a": "M", "endpoint_b": "L"},
        {"id": "p4", "endpoint_a": "M", "endpoint_b": "X"},
        {"id": "p3", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [
        {"id": "V1", "segment_id": "p1", "cost": 1},
        {"id": "V2", "segment_id": "p2", "cost": 2},
    ],
}
FO = solve_failover(NET_FO)
check(FO["feasible"] and FO["closed_valves"] == ["V1", "V2"], "fo plan")
r = plan_monitors(NET_FO, FO["closed_valves"], [
    {"node_id": "M", "fee": 1}, {"node_id": "X", "fee": 1},
    {"node_id": "L", "fee": 1}, {"node_id": "H", "fee": 1},
])
# 关 V1 已把 M/X/L 全隔离 => 正常与 V2 关不上读数完全相同
check(not r["distinguishable"], "fo inherent pair")
pairs = [(p["scenarios"][0]["key"], p["scenarios"][1]["key"])
         for p in r["indistinguishable_pairs"]]
check(("normal", "valve:V2") in pairs, f"fo pairs {pairs}")

print(f"monitors solver tests: {passed} checks passed")
