"""失效监测点布设预览 API 端到端测试: FastAPI TestClient + 临时 SQLite。

重点验证: 独立 API 可用、结果字段完整、可区分/不可区分两种返回、录入校验
422、以及**只读**——不修改当前管网、不写入历史方案、不改变原方案。
"""
import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")

import sys  # noqa: E402
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
from demo import DEMO_NETWORK  # noqa: E402

_client_cm = TestClient(main.app)
client = _client_cm.__enter__()  # 进入生命周期: lifespan 建表

ok = 0


def check(cond, msg):
    global ok
    assert cond, "FAIL: " + msg
    ok += 1


# 双漏点分支网络: S1-V1-M1(X/L1), S2-V2-M2(Y/L2), H 必保
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

# 健康检查
r = client.get("/api/health")
check(r.status_code == 200, "health")

# ---- 常规可行方案上的监测点布设预览(可区分) ----
r = client.post("/api/plan", json=NET)
check(r.status_code == 200, f"plan {r.status_code}")
plan = r.json()
check(plan["feasible"] and plan["closed_valves"] == ["V1", "V2"], "plan saved")
pid = plan["id"]
before = client.get(f"/api/plans/{pid}").json()
plans_before = client.get("/api/plans?limit=100").json()["plans"]
net_before = client.get("/api/network").json()

CANDS = [
    {"node_id": "X", "fee": 2},
    {"node_id": "Y", "fee": 3},
    {"node_id": "M1", "fee": 10},
    {"node_id": "L1", "fee": 10},
    {"node_id": "H", "fee": 1},
]
r = client.post(f"/api/plans/{pid}/monitors", json={"candidates": CANDS})
check(r.status_code == 200, f"monitors status {r.status_code} {r.text}")
res = r.json()
check(res["ok"] and res["plan_id"] == pid, "monitors meta")
check(res["solver"] == "base", "monitors solver field")
check(res["distinguishable"] is True, "distinguishable")
check(res["closed_valves"] == ["V1", "V2"], "closed valves echo")
check(res["selected_ids"] == ["X", "Y"], f"selected {res['selected_ids']}")
check(res["place_cost_total"] == 5, "place cost")
check(res["scenario_count"] == 3, "scenario count")
check(res["candidate_count"] == 5 and res["subset_count"] == 32, "subset meta")
check([p["node_id"] for p in res["selected_points"]] == ["X", "Y"],
      "selected_points")
check([p["fee"] for p in res["selected_points"]] == [2, 3],
      "selected fees")
check(res["indistinguishable_pairs"] == [], "no pairs")
check([sc["key"] for sc in res["scenarios"]]
      == ["normal", "valve:V1", "valve:V2"], "scenario keys")
# 读数验证
def readings(sc):
    return {x["node_id"]: x["wet"] for x in sc["readings"]}

check(readings(res["scenarios"][0])["X"] is False, "normal X dry")
check(readings(res["scenarios"][1])["X"] is True
      and readings(res["scenarios"][1])["Y"] is False, "V1 fail")
check(readings(res["scenarios"][2])["X"] is False
      and readings(res["scenarios"][2])["Y"] is True, "V2 fail")
check(res["scenarios"][1]["segment_id"] == "e1", "v1 segment")
# 每个情形都有全部候选点读数
check(all(len(sc["readings"]) == 5 for sc in res["scenarios"]), "all readings")
check("不修改" in res["message"], "readonly note")

# ---- 只读: 原方案、历史列表、当前管网均未改变 ----
check(client.get(f"/api/plans/{pid}").json() == before, "plan unchanged")
check(client.get("/api/plans?limit=100").json()["plans"] == plans_before,
      "no plan saved by monitors")
check(client.get("/api/network").json() == net_before, "network untouched")

# ---- 不可区分返回: 同签名候选 -> 正常 与 V2 失效不可分 ----
r = client.post(f"/api/plans/{pid}/monitors", json={"candidates": [
    {"node_id": "X", "fee": 1},
    {"node_id": "M1", "fee": 1},
    {"node_id": "L1", "fee": 1},
]})
check(r.status_code == 200, f"pair status {r.status_code}")
res = r.json()
check(res["distinguishable"] is False, "not distinguishable")
check(res["selected_ids"] == [] and res["place_cost_total"] == 0,
      "empty selection")
pairs = [(p["scenarios"][0]["key"], p["scenarios"][1]["key"])
         for p in res["indistinguishable_pairs"]]
check(pairs == [("normal", "valve:V2")], f"pairs {pairs}")
p0 = res["indistinguishable_pairs"][0]["scenarios"]
check(p0[0]["failed_valve"] is None and p0[1]["failed_valve"] == "V2",
      "pair meta")
check("无法区分" in res["message"], "pair message")

# ---- 空请求体: 无候选, 3 种情形全部两两不可分 ----
r = client.post(f"/api/plans/{pid}/monitors", json={})
res = r.json()
check(r.status_code == 200 and res["distinguishable"] is False
      and len(res["indistinguishable_pairs"]) == 3
      and res["subset_count"] == 1, "empty body")

# ---- 备援可行方案 ----
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
r = client.post("/api/plan-failover", json=NET_FO)
fo = r.json()
check(fo["feasible"] and fo["solver"] == "failover", "failover saved")
fid = fo["id"]
# M 点在正常/V2失效时无水, V1失效时有水 -> 可区分 V1; 但正常与 V2 不可分
r = client.post(f"/api/plans/{fid}/monitors", json={"candidates": [
    {"node_id": "M", "fee": 5},
]})
res = r.json()
check(res["solver"] == "failover", "fo solver field")
check(not res["distinguishable"], "fo inherent pair")
pairs = [(p["scenarios"][0]["key"], p["scenarios"][1]["key"])
         for p in res["indistinguishable_pairs"]]
check(pairs == [("normal", "valve:V2")], f"fo pairs {pairs}")

# ---- 不关闭任何阀门的可行方案: 单情形, 空点集费用 0 ----
NET_EMPTY = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "A", "role": "normal"},
    ],
    "segments": [{"id": "p1", "endpoint_a": "S", "endpoint_b": "A"}],
    "valves": [],
}
r = client.post("/api/plan", json=NET_EMPTY)
eid = r.json()["id"]
r = client.post(f"/api/plans/{eid}/monitors",
                json={"candidates": [{"node_id": "A", "fee": 9}]})
res = r.json()
check(r.status_code == 200 and res["distinguishable"] is True
      and res["selected_ids"] == [] and res["place_cost_total"] == 0
      and res["scenario_count"] == 1, "empty-valve plan")

# ---- 方案不存在 ----
r = client.post("/api/plans/9999/monitors", json={"candidates": []})
check(r.status_code == 404, f"404 got {r.status_code}")

# ---- 不可行方案: 不能布设监测点 ----
BAD = {
    "nodes": [
        {"id": "S", "role": "inlet"}, {"id": "L", "role": "leak"},
        {"id": "H", "role": "must_keep"},
    ],
    "segments": [
        {"id": "p1", "endpoint_a": "S", "endpoint_b": "L"},
        {"id": "p2", "endpoint_a": "S", "endpoint_b": "H"},
    ],
    "valves": [],
}
r = client.post("/api/plan", json=BAD)
bad_id = r.json()["id"]
check(r.json()["feasible"] is False, "infeasible plan saved")
r = client.post(f"/api/plans/{bad_id}/monitors", json={"candidates": []})
check(r.status_code == 422, f"infeasible monitors {r.status_code}")
errs = r.json()["detail"]["errors"]
check(any("不可行" in e and str(bad_id) in e for e in errs),
      f"infeasible msg {errs}")

# ---- 录入校验: 422 + detail.errors ----
def expect_422(body, keys):
    r = client.post(f"/api/plans/{pid}/monitors", json=body)
    check(r.status_code == 422, f"422 for {keys}: got {r.status_code} {r.text}")
    errs = r.json()["detail"]["errors"]
    joined = " | ".join(errs)
    for key in keys:
        check(key in joined, f"error contains {key!r}; got: {errs}")


# 候选超过 12 个
expect_422({"candidates": [{"node_id": "X", "fee": 1}] * 13},
           ["超过上限 12"])
# 编号重复
expect_422({"candidates": [
    {"node_id": "X", "fee": 1}, {"node_id": "X", "fee": 2}]},
    ["候选监测点编号重复"])
# 节点不存在 / 进水点 / 缺少编号
expect_422({"candidates": [{"node_id": "ZZ", "fee": 1}]}, ["不存在"])
expect_422({"candidates": [{"node_id": "S1", "fee": 1}]}, ["进水点"])
expect_422({"candidates": [{"fee": 1}]}, ["缺少节点编号"])
# 布设费非法: 0 / 负 / 小数 / 字符串 / bool / null / 缺失
for bad in [0, -2, 1.5, "5", True, None]:
    expect_422({"candidates": [{"node_id": "X", "fee": bad}]},
               ["布设费必须是正整数"])
expect_422({"candidates": [{"node_id": "X"}]}, ["布设费必须是正整数"])

# ---- 示例管网方案: V2 关不上与 V4 关不上时, 漏点 L 都复通且其余节点读数相同 ----
r = client.post("/api/plan", json=DEMO_NETWORK)
did = r.json()["id"]
r = client.post(f"/api/plans/{did}/monitors", json={"candidates": [
    {"node_id": nid, "fee": 1}
    for nid in ("A", "B", "H1", "H2", "L")
]})
res = r.json()
check(not res["distinguishable"], "demo inherent pair")
pairs = [(p["scenarios"][0]["key"], p["scenarios"][1]["key"])
         for p in res["indistinguishable_pairs"]]
check(pairs == [("valve:V2", "valve:V4")], f"demo pairs {pairs}")

# ---- 监测点预览未新增任何历史方案(只读) ----
plans = client.get("/api/plans?limit=100").json()["plans"]
ids = sorted(p["id"] for p in plans)
check(ids == sorted(set([pid, fid, eid, bad_id, did])), f"plan ids {ids}")

print(f"monitors API tests: {ok} checks passed")
