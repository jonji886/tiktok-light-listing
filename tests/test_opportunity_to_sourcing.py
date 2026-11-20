# -*- coding: utf-8 -*-
"""opportunity_to_sourcing 离线回归测试：判读→检索计划→候选输出 的编排逻辑。

2026-11-20 双通道版：
  · aibuy fallback 路径：子进程调用全部 mock（同 2026-11-19 原测试，显式 --channel aibuy）。
  · linkfox 主通道：plan 出任务清单（不发网络）；read 用临时落盘 JSON mock MCP 产物。
真实通道验证在 2026-11-19（aibuy）与 2026-11-20（linkfox dld/imageSearch 各 1 次实调）人工完成，
本测试锁的是编排行为。
"""
import csv
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import ROOT, SCRIPTS  # noqa: F401,E402

sys.path.insert(0, SCRIPTS)
import opportunity_to_sourcing as ots  # noqa: E402

FAKE_ROWS = [
    {"offerId": "111", "title": "围裙A", "price": "6.8", "monthSold": "50",
     "beginQuantity": "1", "sourceFactory": "TRUE", "companyName": "x",
     "link": "https://detail.1688.com/offer/111.html"},
    {"offerId": "222", "title": "围裙B", "price": "9.9", "monthSold": "300",
     "beginQuantity": "2", "sourceFactory": "FALSE", "companyName": "y",
     "link": "https://detail.1688.com/offer/222.html"},
    {"offerId": "111", "title": "围裙A-dup", "price": "7.0", "monthSold": "80",
     "beginQuantity": "1", "sourceFactory": "TRUE", "companyName": "x",
     "link": "https://detail.1688.com/offer/111.html"},
]


class TestDedupSort(unittest.TestCase):
    def test_dedup_keeps_max_month_sold(self):
        picks = ots.dedup_sort(FAKE_ROWS, top=5)
        self.assertEqual([p["offerId"] for p in picks], ["222", "111"])
        self.assertEqual(picks[1]["monthSold"], "80")   # 同 ID 保留高销行

    def test_top_limit(self):
        self.assertEqual(len(ots.dedup_sort(FAKE_ROWS, top=1)), 1)


class TestOrchestration(unittest.TestCase):
    def _write_csv(self, rows, header):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "opp.csv")
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
        return p

    def _run(self, rows, calls, argv_extra=()):
        header = ["title", "sales", "price_ref", "category", "source_site",
                  "tiktok_metrics", "image_url", "search_keyword"]
        csvp = self._write_csv(rows, header)
        outp = os.path.join(os.path.dirname(csvp), "sourcing.csv")
        captured = []

        def fake_run_aibuy(args, timeout=90):
            captured.append(args)
            kind = "search-image" if args[0] == "search-image" else "search"
            return True, FAKE_ROWS if kind in calls else [], ""
        orig = ots.run_aibuy
        ots.run_aibuy = fake_run_aibuy
        try:
            argv = [sys.argv[0], "--csv", csvp, "--out", outp, "--channel", "aibuy"] + list(argv_extra)
            sys.argv = argv
            rc = ots.main()
        finally:
            ots.run_aibuy = orig
            sys.argv = argv
        out_rows = []
        if os.path.exists(outp):
            with open(outp, encoding="utf-8-sig") as f:
                out_rows = list(csv.DictReader(f))
        return rc, out_rows, captured

    _CAND = {"title": "Multi-Pocket Apron Chef Kitchen", "sales": "3.2k+",
             "price_ref": "RM5.00 - RM12.00",
             "category": "Bekalan Rumah > Dapur > Apron", "source_site": "MY",
             "tiktok_metrics": "yes", "image_url": "", "search_keyword": "围裙 厨房"}

    def test_reject_skips_search(self):
        row = dict(self._CAND, title="WATSONS Pumice Stone 1S", search_keyword="磨脚石")
        rc, out_rows, captured = self._run([row], calls={"search"})
        self.assertEqual(captured, [])            # 不发任何检索
        self.assertEqual(out_rows, [])

    def test_candidate_keyword_search(self):
        rc, out_rows, captured = self._run([dict(self._CAND)], calls={"search"})
        self.assertEqual(rc, 0)
        self.assertEqual(captured[0][0], "search")
        self.assertIn("围裙 厨房", captured[0])
        # top 默认 8，样本 2 个唯一 ID → 2 行；offer 字段齐全
        self.assertEqual({r["offer_id"] for r in out_rows}, {"222", "111"})
        self.assertTrue(all(r["verdict"] == "candidate" for r in out_rows))

    def test_need_input_no_keyword_no_image(self):
        row = dict(self._CAND, price_ref="", tiktok_metrics="unknown")
        # 无图无词 → NEED_INPUT 行，不发检索
        row = dict(row, search_keyword="", image_url="")
        rc, out_rows, captured = self._run([row], calls={"search", "search-image"})
        self.assertEqual(captured, [])
        self.assertEqual(len(out_rows), 1)
        self.assertIn("NEED_INPUT", out_rows[0]["next_step"])

    def test_image_mode_uses_search_image(self):
        row = dict(self._CAND, search_keyword="",
                   image_url="https://cbu01.alicdn.com/x.jpg")
        rc, out_rows, captured = self._run([row], calls={"search-image"})
        self.assertEqual(captured[0][0], "search-image")
        self.assertIn("https://cbu01.alicdn.com/x.jpg", captured[0])

    def test_next_step_points_to_selection_intake(self):
        _, out_rows, _ = self._run([dict(self._CAND)], calls={"search"})
        for r in out_rows:
            self.assertIn("selection_intake.py image --ids", r["next_step"])
            self.assertIn("--region MY", r["next_step"])

    def test_dry_run_writes_nothing(self):
        rc, out_rows, captured = self._run([dict(self._CAND)], calls={"search"},
                                           argv_extra=("--dry-run",))
        self.assertEqual(captured, [])
        self.assertEqual(out_rows, [])


# ─────────────── LinkFox 主通道（2026-11-20 切换） ───────────────
import types  # noqa: E402

FAKE_LF_IMAGE = {"errcode": 200, "errmsg": "ok", "products": [
    {"offerId": "972899576020", "title": "磁吸针线盒 A", "price": 5.9,
     "salesQuantity": 12431, "quantityBegin": 1, "company": "金华市示物贸易有限公司",
     "productUrl": "https://detail.1688.com/offer/972899576020.html",
     "imageUrl": "https://cbu01.alicdn.com/a.jpg",
     "sendGoodsAddressText": "浙江省金华市", "consignPrice": 8.0,
     "sellerIdentities": "诚信通会员"},
    {"offerId": "921346960980", "title": "磁吸针线盒 B", "price": 4.7,
     "salesQuantity": 4667, "quantityBegin": 1,
     "productUrl": "https://detail.1688.com/offer/921346960980.html",
     "imageUrl": "https://cbu01.alicdn.com/b.jpg"},  # 无发货地 → 留空不猜
]}
FAKE_LF_KW = {"errcode": 200, "products": [
    {"offerId": "735283254705", "title": "收纳箱 C", "price": 2.98,
     "salesQuantity": 14536, "quantityBegin": 2, "company": "揭阳市万佳芸贸易有限公司",
     "asinUrl": "https://detail.1688.com/offer/735283254705.html",
     "imageUrl": "https://cbu01.alicdn.com/c.jpg", "deliveryTime": "48"},
]}


class TestLinkFoxChannel(unittest.TestCase):
    def _write_csv(self, rows):
        d = tempfile.mkdtemp()
        header = ["title", "sales", "price_ref", "category", "source_site",
                  "tiktok_metrics", "image_url", "search_keyword"]
        p = os.path.join(d, "opp.csv")
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
        return p

    def test_plan_writes_tasks_without_network(self):
        csvp = self._write_csv([dict(TestOrchestration._CAND, search_keyword="围裙 厨房",
                                     image_url="https://obs.local/card/x.jpg")])
        outp = os.path.join(os.path.dirname(csvp), "sourcing.csv")
        tfp = os.path.join(os.path.dirname(csvp), "tasks.json")
        argv = [sys.argv[0], "--csv", csvp, "--out", outp,
                "--channel", "linkfox", "--linkfox", "plan"]
        orig_tasks, orig_mcp = ots.LINKFOX_TASKS, ots.LINKFOX_MCP_DIR
        ots.LINKFOX_TASKS, ots.LINKFOX_MCP_DIR = tfp, os.path.dirname(csvp)
        try:
            rc = self._main_with(argv)
        finally:
            ots.LINKFOX_TASKS, ots.LINKFOX_MCP_DIR = orig_tasks, orig_mcp
        self.assertEqual(rc, 0)
        with open(tfp, encoding="utf-8") as f:
            tasks = json.load(f)
        self.assertEqual({t["toolName"] for t in tasks},
                         {"alibaba1688/imageSearch", "dld/productSearch"})
        kw_task = next(t for t in tasks if t["mode"] == "keyword")
        self.assertEqual(kw_task["arguments"]["keyWord"], "围裙 厨房")
        self.assertFalse(os.path.exists(outp))  # plan 不产候选

    def _main_with(self, argv):
        old = sys.argv
        sys.argv = argv
        try:
            return ots.main()
        finally:
            sys.argv = old

    def test_read_from_saved_payloads(self):
        csvp = self._write_csv([dict(TestOrchestration._CAND, search_keyword="围裙 厨房",
                                     image_url="")])
        d = os.path.dirname(csvp)
        mcp = os.path.join(d, "mcp")
        os.makedirs(mcp)
        kwp = os.path.join(mcp, "kw_围裙_厨房.json")
        with open(kwp, "w", encoding="utf-8") as f:
            json.dump(FAKE_LF_KW, f)
        tasks = [{"task_id": "kw_x", "mode": "keyword", "toolName": "dld/productSearch",
                  "arguments": {}, "save_to": kwp,
                  "opportunity_title": TestOrchestration._CAND["title"],
                  "verdict": "candidate", "source_site": "MY"}]
        tfp = os.path.join(d, "tasks.json")
        with open(tfp, "w", encoding="utf-8") as f:
            json.dump(tasks, f)
        outp = os.path.join(d, "sourcing.csv")
        orig_tasks, orig_mcp = ots.LINKFOX_TASKS, ots.LINKFOX_MCP_DIR
        ots.LINKFOX_TASKS = tfp
        try:
            rc = self._main_with([sys.argv[0], "--csv", csvp, "--out", outp,
                                  "--channel", "linkfox", "--linkfox", "read"])
        finally:
            ots.LINKFOX_TASKS = orig_tasks
        self.assertEqual(rc, 0)
        with open(outp, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["data_source"], "linkfox")
        self.assertEqual(r["offer_id"], "735283254705")
        self.assertEqual(r["month_sold"], "14536")
        self.assertEqual(r["company"], "揭阳市万佳芸贸易有限公司")
        self.assertEqual(r["ship_city_raw"], "")  # dld 无发货地字段 → 留空，不猜

    def test_read_missing_saved_is_fail_not_crash(self):
        csvp = self._write_csv([dict(TestOrchestration._CAND, search_keyword="围裙 厨房",
                                     image_url="")])
        d = os.path.dirname(csvp)
        tfp = os.path.join(d, "tasks.json")
        with open(tfp, "w", encoding="utf-8") as f:
            json.dump([{"task_id": "kw_x", "mode": "keyword",
                        "toolName": "dld/productSearch", "arguments": {},
                        "save_to": os.path.join(d, "nonexist.json"),
                        "opportunity_title": TestOrchestration._CAND["title"],
                        "verdict": "candidate", "source_site": "MY"}], f)
        outp = os.path.join(d, "sourcing.csv")
        orig_tasks = ots.LINKFOX_TASKS
        ots.LINKFOX_TASKS = tfp
        try:
            rc = self._main_with([sys.argv[0], "--csv", csvp, "--out", outp,
                                  "--channel", "linkfox", "--linkfox", "read"])
        finally:
            ots.LINKFOX_TASKS = orig_tasks
        self.assertEqual(rc, 3)  # 全部读取失败 → 3（对应"通道未就绪"）
        with open(outp, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertIn("SEARCH_FAIL(linkfox)", rows[0]["next_step"])
        self.assertIn("--channel aibuy", rows[0]["next_step"])

    def test_ship_city_passthrough_and_no_rerank_by_default(self):
        # imageSearch 落盘带发货地 → ship_city_raw 原样带出；排序保留检索原序
        self.maxDiff = None
        rows = ots.linkfox_to_rows(FAKE_LF_IMAGE["products"])
        self.assertEqual(rows[0]["shipCityRaw"], "浙江省金华市")
        self.assertEqual(rows[1]["shipCityRaw"], "")
        picks = ots.rank_rows(rows, top=8, rank_by="source")
        # source=严格保留检索原序，即使 B 无销量字段也不后置/不重排
        self.assertEqual([p["offerId"] for p in picks],
                         ["972899576020", "921346960980"])
        # sales=显式按月销量倒序（同通道内比较才允许）
        swapped = list(reversed(rows))
        ranked = ots.rank_rows(swapped, top=8, rank_by="sales")
        self.assertEqual(ranked[0]["offerId"], "972899576020")

    def test_city_from_location_normalizes(self):
        # linkfox 证据进候选表时保留原文，归一化由 selection_intake.city_from_location 做
        from selection_intake import city_from_location
        self.assertEqual(city_from_location("浙江省金华市"), "金华市")
        self.assertEqual(city_from_location("河北省石家庄市"), "石家庄市")
        self.assertIsNone(city_from_location(""))

    def test_read_offers_maps_linkfox_columns(self):
        import selection_intake as si
        d = tempfile.mkdtemp()
        p = os.path.join(d, "sourcing.csv")
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["offer_id", "offer_title", "start_price_cny",
                                              "begin_qty", "company", "image_url",
                                              "ship_city_raw", "data_source"])
            w.writeheader()
            w.writerow({"offer_id": "972899576020", "offer_title": "磁吸针线盒 A",
                        "start_price_cny": "5.9", "begin_qty": "1",
                        "company": "金华市示物贸易有限公司",
                        "image_url": "https://cbu01.alicdn.com/a.jpg",
                        "ship_city_raw": "浙江省金华市", "data_source": "linkfox"})
        args = types.SimpleNamespace(ids="", ids_file="", from_csv=p)
        recs = si.read_offers(args)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["source"], "linkfox")
        self.assertEqual(r["cost_cny"], 5.9)
        self.assertEqual(r["seller"], "金华市示物贸易有限公司")
        self.assertEqual(r["ship_city_evidence"], "金华市")  # 已归一化
        item = si.merge_offer(r, None)
        self.assertEqual(item["data_source"], "linkfox")
        self.assertEqual(item["ship_city"], "金华市")
        self.assertEqual(item["ship_city_source"], "linkfox")


class TestBailianChannel(unittest.TestCase):
    """bailian 通道编排（不发网络：mock bailian_sourcing 的 load_key/find_product）。"""

    FAKE_ITEMS = [
        {"itemId": "842414396564", "title": "围裙防水 口袋款", "currentPrice": 6.8,
         "quantityBegin": 1, "storeAmount": 5000,
         "serviceTags": {"is_late_ship_compensate": "Y", "lgt_3m_24h_avg": "0.92",
                          "is_has_inventory": "Y"},
         "oldReputationTags": {"pay_ord_cnt_30d": "385", "rebuy_cnt_30d": "384"},
         "company": "郑州宽昭服装贸易有限公司",
         "detailUrl": "https://detail.1688.com/offer/842414396564.html",
         "imageUrl": "https://cbu01.alicdn.com/x.jpg", "score": 0.99,
         "recallSource": "text_recall_yx_high_score"},
        {"itemId": "1075567212128", "title": "围裙 无赔付 零揽收数据", "currentPrice": 9.0,
         "quantityBegin": 2,
         "serviceTags": {"is_late_ship_compensate": "N", "lgt_3m_24h_avg": "0.0"},
         "oldReputationTags": {"pay_ord_cnt_30d": "0"},
         "company": "某新厂", "detailUrl": "https://detail.1688.com/offer/1075567212128.html",
         "imageUrl": "https://cbu01.alicdn.com/y.jpg", "score": 0.9},
    ]

    def _run(self, rows, argv_extra=()):
        import bailian_sourcing as bs
        d = tempfile.mkdtemp()
        header = ["title", "sales", "price_ref", "category", "source_site",
                  "tiktok_metrics", "image_url", "search_keyword"]
        csvp = os.path.join(d, "opp.csv")
        with open(csvp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
        outp = os.path.join(d, "sourcing.csv")
        calls = []
        orig_k, orig_f = bs.load_key, bs.find_product
        bs.load_key = lambda: "sk-fake"
        bs.find_product = lambda key, args: (calls.append(args), self.FAKE_ITEMS)[1]
        old = sys.argv
        sys.argv = [sys.argv[0], "--csv", csvp, "--out", outp, "--channel", "bailian"] + list(argv_extra)
        try:
            rc = ots.main()
        finally:
            sys.argv, bs.load_key, bs.find_product = old, orig_k, orig_f
        out_rows = []
        if os.path.exists(outp):
            with open(outp, encoding="utf-8-sig") as f:
                out_rows = list(csv.DictReader(f))
        return rc, out_rows, calls

    def test_keyword_search_produces_machine_columns(self):
        rc, rows, calls = self._run([dict(TestOrchestration._CAND)])
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0]["query"], "围裙 厨房")
        self.assertEqual({r["offer_id"] for r in rows},
                         {"842414396564", "1075567212128"})
        good = next(r for r in rows if r["offer_id"] == "842414396564")
        self.assertEqual(good["data_source"], "bailian")
        self.assertEqual(good["late_ship_compensate"], "Y")
        self.assertEqual(good["pickup_24h_rate"], "0.92")
        self.assertEqual(good["month_sold"], "385")      # bailian 口径=pay_ord_cnt_30d
        self.assertEqual(good["ship_city_raw"], "")       # 无城市，留空不猜
        bad = next(r for r in rows if r["offer_id"] == "1075567212128")
        self.assertEqual(bad["pickup_24h_rate"], "0.0")   # 原值透传，UNKNOWN 判定归 gate

    def test_reject_skips_bailian(self):
        row = dict(TestOrchestration._CAND, title="WATSONS Pumice Stone 1S")
        rc, rows, calls = self._run([row])
        self.assertEqual(calls, [])
        self.assertEqual(rows, [])

    def test_dry_run_no_calls(self):
        rc, rows, calls = self._run([dict(TestOrchestration._CAND)], argv_extra=("--dry-run",))
        self.assertEqual(calls, [])
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
