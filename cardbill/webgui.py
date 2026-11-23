"""信用卡账单管理与还款提醒系统 —— 零依赖 Web 图形界面（备选入口）。

为什么需要它
------------
tkinter 是 Python 标准库中的「可选组件」，WorkBuddy 精简版 / 嵌入式发行版往往
不带该模块，导致 ``python -m cardbill.gui`` 无法启动。本模块用标准库
``http.server`` + 原生 HTML/CSS/JS 提供一个等价的可操作界面：

    * 零第三方依赖（不引入 Flask / Django 等）；
    * 不导入 tkinter，可在缺少 tkinter 的环境真实启动；
    * 所有业务动作复用 ``cardbill.gui.CardBillController``，与桌面版、CLI
      共用同一套领域逻辑与同一个 SQLite 数据库。

启动方式::

    python -m cardbill.webgui                    # 默认 http://127.0.0.1:8766
    python -m cardbill.webgui --port 8766        # 明确端口
    python -m cardbill.webgui --host 127.0.0.1 --db data/cardbill.db

启动后用浏览器访问 ``http://127.0.0.1:<port>`` 即可操作；按 Ctrl+C 停止服务。
本服务仅绑定本机回环地址（默认 127.0.0.1），不会暴露到局域网。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

if __package__:
    from . import __version__
    from .gui import DEFAULT_DB, PROJECT_ROOT, CardBillController
else:  # pragma: no cover - 仅在直接执行脚本时触发
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from cardbill import __version__
    from cardbill.gui import DEFAULT_DB, PROJECT_ROOT, CardBillController


# ---------------------------------------------------------------------------
# 单页前端：原生 HTML / CSS / JS，无任何外部资源，可完全离线使用。
# ---------------------------------------------------------------------------
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>信用卡账单管理与还款提醒系统 · Web 版</title>
<style>
:root { --bg:#f4f6f9; --card:#fff; --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb;
        --brand:#2563eb; --ok:#15803d; --warn:#b45309; }
* { box-sizing:border-box; }
body { margin:0; font-family:"Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;
       color:var(--ink); background:var(--bg); }
header { background:linear-gradient(135deg,#1e3a8a,#2563eb); color:#fff; padding:16px 28px; }
header h1 { margin:0 0 6px; font-size:19px; }
header p { margin:0; opacity:.85; font-size:12.5px; }
main { max-width:1180px; margin:20px auto; padding:0 20px 40px; }
.panel { background:var(--card); border:1px solid var(--line); border-radius:10px;
         margin-bottom:18px; box-shadow:0 1px 3px rgba(0,0,0,.05); }
.panel h2 { margin:0; padding:12px 18px; font-size:15px; border-bottom:1px solid var(--line);
            background:#f9fafb; border-radius:10px 10px 0 0; }
.panel h3 { margin:14px 0 8px; font-size:13px; color:var(--muted); }
.body { padding:16px 18px; }
.row { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; margin-bottom:12px; }
.field { display:flex; flex-direction:column; gap:4px; }
.field label { font-size:12px; color:var(--muted); }
input, select { padding:7px 9px; border:1px solid #d1d5db; border-radius:6px;
                font-size:13px; font-family:inherit; background:#fff; }
input:focus, select:focus { outline:2px solid #93c5fd; border-color:var(--brand); }
button { padding:8px 14px; border:0; border-radius:6px; background:var(--brand);
         color:#fff; font-size:13px; cursor:pointer; font-family:inherit; }
button:hover { filter:brightness(.94); }
button.ghost { background:#eef2ff; color:var(--brand); }
button.small { padding:4px 9px; font-size:12px; }
.tablewrap { overflow:auto; max-height:320px; border:1px solid var(--line); border-radius:6px; }
table { width:100%; border-collapse:collapse; font-size:12.5px; }
th, td { padding:7px 10px; text-align:left; white-space:nowrap; border-bottom:1px solid var(--line); }
th { background:#f3f4f6; position:sticky; top:0; z-index:1; }
tr:last-child td { border-bottom:0; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
.paid { color:var(--ok); font-weight:600; }
.unpaid { color:var(--warn); font-weight:600; }
.empty { color:var(--muted); text-align:center; padding:18px; }
.hint { color:var(--muted); font-size:12px; margin:6px 0 0; }
pre.result { margin:10px 0 0; padding:10px 12px; background:#f8fafc; border:1px solid var(--line);
             border-radius:6px; white-space:pre-wrap; word-break:break-all;
             font-size:12.5px; min-height:34px; }
#log { max-height:150px; overflow:auto; background:#0f172a; color:#d1fae5;
       border-radius:6px; padding:10px 12px; font:12px/1.6 Consolas,Monaco,monospace; }
.log-line.error { color:#fecaca; }
.badge { display:inline-block; padding:2px 7px; border-radius:10px;
         background:rgba(255,255,255,.2); font-size:11px; vertical-align:middle; }
</style>
</head>
<body>
<header>
  <h1>信用卡账单管理与还款提醒系统 <span class="badge">Web 版 · 零依赖</span></h1>
  <p>数据库：<span id="dbPath">加载中…</span> ｜ 全部功能复用既有领域逻辑，与桌面版 / CLI 共用同一数据库</p>
</header>
<main>

<section class="panel">
  <h2>① 卡片管理</h2>
  <div class="body">
    <div class="row">
      <div class="field"><label>卡号 / ID</label><input id="cId" size="14"></div>
      <div class="field"><label>卡片名称</label><input id="cName" size="20"></div>
      <div class="field"><label>账单日(1-31)</label><input id="cDay" value="5" size="7"></div>
      <div class="field"><label>宽限期(天)</label><input id="cOffset" value="20" size="7"></div>
      <div class="field"><label>最低比例</label><input id="cRate" value="0.10" size="8"></div>
      <div class="field"><label>最低下限</label><input id="cFloor" value="10.00" size="9"></div>
      <button id="btnAddCard">添加卡片</button>
      <button id="btnRefreshCards" class="ghost">刷新列表</button>
    </div>
    <div class="tablewrap"><table>
      <thead><tr><th>卡号</th><th>卡片名称</th><th>账单日</th><th>宽限期(天)</th>
              <th>最低比例</th><th>最低下限</th><th>币种</th></tr></thead>
      <tbody id="cardsBody"></tbody>
    </table></div>
    <p class="hint">账单日若当月不存在（如 31 号遇 2 月），系统自动吸附到月末。</p>
  </div>
</section>

<section class="panel">
  <h2>② 账单导入</h2>
  <div class="body">
    <div class="row">
      <div class="field"><label>样例文件（选择后自动填入路径）</label><select id="sampleFile"></select></div>
      <div class="field" style="flex:1;min-width:300px"><label>CSV 路径（相对路径基于项目根目录）</label>
        <input id="csvPath" style="width:100%" placeholder="data/sample_bills/CARD_A.csv"></div>
      <button id="btnImport">导入 CSV</button>
      <button id="btnRebuild" class="ghost">重算账单</button>
    </div>
    <pre class="result" id="importResult">尚未导入。</pre>
    <p class="hint">表头：card_id,txn_date,amount,description,signature；导入后自动去重并重算全部账单。</p>
  </div>
</section>

<section class="panel">
  <h2>③ 账单查询</h2>
  <div class="body">
    <div class="row">
      <div class="field"><label>卡片</label><select id="qCard"><option value="">全部</option></select></div>
      <div class="field"><label>账单月（留空=全部，YYYY-MM）</label><input id="qMonth" placeholder="2025-02" size="12"></div>
      <button id="btnQuery">查询账单</button>
      <button id="btnQueryAll" class="ghost">查询全部</button>
    </div>
    <div class="tablewrap"><table>
      <thead><tr><th>卡号</th><th>账单月</th><th>账单日</th><th>到期日</th>
              <th>最长免息期(天)</th><th>应还</th><th>最低还款</th><th>手续费</th>
              <th>状态</th><th>操作</th></tr></thead>
      <tbody id="billsBody"></tbody>
    </table></div>
  </div>
</section>

<section class="panel">
  <h2>④ 还款提醒</h2>
  <div class="body">
    <div class="row">
      <div class="field"><label>今日（留空=系统当天，YYYY-MM-DD）</label><input id="rToday" placeholder="2025-02-18" size="15"></div>
      <div class="field"><label>提前天数</label><input id="rLead" value="7" size="6"></div>
      <button id="btnCheck">检查今日提醒</button>
      <button id="btnRefreshLedger" class="ghost">刷新提醒台账</button>
    </div>
    <h3>本次新增提醒（FULL 全额 / MINIMUM 最低，重复点击幂等）</h3>
    <div class="tablewrap"><table>
      <thead><tr><th>卡号</th><th>账单周期</th><th>策略</th><th>金额</th><th>距到期(天)</th><th>到期日</th></tr></thead>
      <tbody id="remindersBody"></tbody>
    </table></div>
    <h3>标记已还（此后该期停止提醒）</h3>
    <div class="row">
      <div class="field"><label>卡号</label><input id="pCard" size="15"></div>
      <div class="field"><label>账单月（YYYY-MM）</label><input id="pMonth" placeholder="2025-02" size="12"></div>
      <button id="btnMarkPaid">标记已还</button>
    </div>
    <h3>历史提醒台账（审计用，标记已还不会删除）</h3>
    <div class="tablewrap"><table>
      <thead><tr><th>卡号</th><th>账单周期</th><th>策略</th><th>到期日</th><th>金额</th><th>创建时间</th></tr></thead>
      <tbody id="ledgerBody"></tbody>
    </table></div>
  </div>
</section>

<section class="panel">
  <h2>操作日志（每次操作的结果反馈）</h2>
  <div class="body"><div id="log"></div></div>
</section>

</main>
<script>
const $ = (id) => document.getElementById(id);

function log(msg, kind) {
  const line = document.createElement("div");
  line.className = "log-line" + (kind ? " " + kind : "");
  line.textContent = "[" + new Date().toLocaleTimeString() + "] " + msg;
  const box = $("log");
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
}

async function api(path, options) {
  try {
    const resp = await fetch(path, Object.assign(
      { headers: { "Content-Type": "application/json" } }, options || {}
    ));
    const payload = await resp.json();
    if (!resp.ok || payload.ok === false) {
      log(payload.error || payload.message || ("HTTP " + resp.status), "error");
      return null;
    }
    if (payload.message) log(payload.message, "ok");
    return payload.data;
  } catch (e) {
    log("请求失败: " + e, "error");
    return null;
  }
}

function post(path, obj) {
  return api(path, { method: "POST", body: JSON.stringify(obj) });
}

function renderTable(tbodyId, rows, columns) {
  const tbody = $(tbodyId);
  tbody.replaceChildren();
  if (!rows || rows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = columns.length;
    td.className = "empty";
    td.textContent = "（暂无数据）";
    tr.appendChild(td); tbody.appendChild(tr);
    return;
  }
  rows.forEach(function (row, idx) {
    const tr = document.createElement("tr");
    columns.forEach(function (col) {
      const td = document.createElement("td");
      if (col.cls) td.className = col.cls(row);
      const value = col.render ? col.render(row, idx) : (row[col.key] == null ? "" : row[col.key]);
      if (value instanceof Node) td.appendChild(value);
      else td.textContent = value;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
}

const cardCols = [
  { key: "id", label: "卡号" }, { key: "name", label: "名称" },
  { key: "statement_day", label: "账单日" }, { key: "due_offset_days", label: "宽限期" },
  { key: "min_payment_rate", label: "最低比例" }, { key: "min_payment_floor", label: "最低下限" },
  { key: "currency", label: "币种" }
];
const billCols = [
  { key: "card_id" }, { key: "billing_month" }, { key: "statement_date" }, { key: "due_date" },
  { key: "free_days", cls: function(){ return "num"; } },
  { key: "total_due", cls: function(){ return "num"; } },
  { key: "minimum_payment", cls: function(){ return "num"; } },
  { key: "installment_fee", cls: function(){ return "num"; } },
  { key: "paid", cls: function(r){ return r.paid === "已还" ? "paid" : "unpaid"; } },
  { render: function (row) {
      const b = document.createElement("button");
      b.className = "small ghost";
      b.textContent = "标记已还";
      b.onclick = function () { fillPaid(row.card_id, row.billing_month); };
      return b;
    } }
];
const reminderCols = [
  { key: "card_id" }, { key: "billing_cycle" }, { key: "strategy" },
  { key: "amount", cls: function(){ return "num"; } },
  { key: "days_to_due", cls: function(){ return "num"; } }, { key: "due_date" }
];
const ledgerCols = [
  { key: "card_id" }, { key: "billing_cycle" }, { key: "strategy" },
  { key: "due_date" }, { key: "amount", cls: function(){ return "num"; } }, { key: "created_at" }
];

let cardsCache = [];
async function refreshCards() {
  cardsCache = await api("/api/cards") || [];
  renderTable("cardsBody", cardsCache, cardCols);
  const sel = $("qCard");
  const keep = sel.value;
  sel.replaceChildren();
  const all = document.createElement("option");
  all.value = ""; all.textContent = "全部"; sel.appendChild(all);
  cardsCache.forEach(function (c) {
    const o = document.createElement("option");
    o.value = c.id; o.textContent = c.id + " · " + c.name; sel.appendChild(o);
  });
  sel.value = cardsCache.some(function (c) { return c.id === keep; }) ? keep : "";
  log("卡片列表已刷新：" + cardsCache.length + " 张。");
}

async function queryBills() {
  const params = new URLSearchParams();
  params.set("card_id", $("qCard").value);
  params.set("month", $("qMonth").value.trim());
  const rows = await api("/api/bills?" + params.toString()) || [];
  renderTable("billsBody", rows, billCols);
  log("查询账单：" + rows.length + " 期（卡片=" + ($("qCard").value || "全部") +
      "，月份=" + ($("qMonth").value.trim() || "全部") + "）。");
}

async function refreshLedger() {
  const rows = await api("/api/reminders") || [];
  renderTable("ledgerBody", rows, ledgerCols);
}

async function refreshSamples() {
  const files = await api("/api/sample-files") || [];
  const sel = $("sampleFile");
  sel.replaceChildren();
  const none = document.createElement("option");
  none.value = ""; none.textContent = "（请选择样例文件）"; sel.appendChild(none);
  files.forEach(function (f) {
    const o = document.createElement("option");
    o.value = f; o.textContent = f.split("/").pop(); sel.appendChild(o);
  });
}

function fillPaid(cardId, month) {
  $("pCard").value = cardId;
  $("pMonth").value = month;
  $("btnMarkPaid").scrollIntoView({ behavior: "smooth", block: "center" });
  log("已将 " + cardId + " " + month + " 带入「标记已还」表单，请确认后点击按钮。");
}

$("btnAddCard").onclick = async function () {
  const ok = await post("/api/cards", {
    card_id: $("cId").value, name: $("cName").value,
    statement_day: $("cDay").value, due_offset_days: $("cOffset").value,
    min_rate: $("cRate").value, min_floor: $("cFloor").value
  });
  if (ok) { $("cId").value = ""; $("cName").value = ""; await refreshCards(); }
};
$("btnRefreshCards").onclick = refreshCards;

$("sampleFile").onchange = function () { if (this.value) $("csvPath").value = this.value; };
$("btnImport").onclick = async function () {
  if (!$("csvPath").value.trim()) { log("请先选择样例文件或填写 CSV 路径。", "error"); return; }
  const data = await post("/api/import", { csv_path: $("csvPath").value });
  if (data) {
    $("importResult").textContent = data.message || "";
    await refreshCards(); await queryBills();
  }
};
$("btnRebuild").onclick = async function () {
  const data = await post("/api/rebuild", {});
  if (data) { $("importResult").textContent = data.message || ""; await queryBills(); }
};

$("btnQuery").onclick = queryBills;
$("btnQueryAll").onclick = function () { $("qCard").value = ""; $("qMonth").value = ""; queryBills(); };

$("btnCheck").onclick = async function () {
  const data = await post("/api/reminders/check", {
    today: $("rToday").value, lead_days: $("rLead").value
  });
  if (data) {
    renderTable("remindersBody", data.reminders, reminderCols);
    log("本次新增提醒：" + data.reminders.length + " 条。", "ok");
    await refreshLedger();
  }
};
$("btnRefreshLedger").onclick = refreshLedger;
$("btnMarkPaid").onclick = async function () {
  const ok = await post("/api/reminders/mark-paid", {
    card_id: $("pCard").value, billing_month: $("pMonth").value
  });
  if (ok) { await queryBills(); await refreshLedger(); }
};

(async function init() {
  const status = await api("/api/status");
  if (status) $("dbPath").textContent = status.db_path;
  await refreshSamples(); await refreshCards(); await queryBills(); await refreshLedger();
  renderTable("remindersBody", [], reminderCols);
  log("界面就绪。数据库与桌面版 / CLI 共用，Web 版无需 tkinter。", "ok");
})();
</script>
</body>
</html>
"""


class WebHandler(BaseHTTPRequestHandler):
    """把每个按钮动作映射为一条 JSON API；前端只用 fetch 调用。"""

    server_version = "CardBillWebGUI/1.0"

    @property
    def controller(self) -> CardBillController:
        return self.server.controller  # type: ignore[attr-defined]

    # ------------------------------------------------------------ 响应工具
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _ok(self, data=None, message: str = "") -> None:
        self._send_json({"ok": True, "data": data, "message": message})

    def _fail(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    @staticmethod
    def _resolve_csv(raw) -> Path:
        """相对路径基于项目根目录解析，便于浏览器直接填 data/sample_bills/..."""
        path = Path(str(raw or "").strip().strip('"'))
        return path if path.is_absolute() else PROJECT_ROOT / path

    # ------------------------------------------------------------ 统一分发
    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        route = parsed.path.rstrip("/") or "/"
        try:
            if method == "GET":
                self._route_get(route, parsed.query)
            else:
                self._route_post(route)
        except (ValueError, KeyError, TypeError) as exc:
            self._fail(f"参数错误：{exc}", 400)
        except Exception as exc:  # noqa: BLE001 - 保证 API 永不因异常中断
            self._fail(f"服务器内部错误：{type(exc).__name__}: {exc}", 500)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    # ------------------------------------------------------------ GET 路由
    def _route_get(self, route: str, query: str) -> None:
        if route in ("/", "/index.html"):
            self._send_html(INDEX_HTML)
        elif route == "/api/status":
            self._ok({
                "db_path": self.controller.db_path,
                "version": __version__,
                "host": self.server.server_address[0],  # type: ignore[attr-defined]
                "port": self.server.server_address[1],  # type: ignore[attr-defined]
            })
        elif route == "/api/cards":
            self._ok(self.controller.handle_list_cards())
        elif route == "/api/bills":
            params = parse_qs(query)
            card_id = params.get("card_id", [""])[0]
            month = params.get("month", [""])[0]
            self._ok(self.controller.handle_query_bills(card_id, month))
        elif route == "/api/reminders":
            self._ok(self.controller.handle_list_reminders())
        elif route == "/api/sample-files":
            sample_dir = PROJECT_ROOT / "data" / "sample_bills"
            files = (
                [str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
                 for p in sorted(sample_dir.glob("*.csv"))]
                if sample_dir.is_dir() else []
            )
            self._ok(files)
        else:
            self._fail(f"未知路由：GET {route}", 404)

    # ------------------------------------------------------------ POST 路由
    def _route_post(self, route: str) -> None:
        body = self._body()
        ctl = self.controller

        if route == "/api/cards":
            msg = ctl.handle_add_card(
                body.get("card_id", ""), body.get("name", ""),
                body.get("statement_day", ""), body.get("due_offset_days", ""),
                body.get("min_rate", "0.10"), body.get("min_floor", "10.00"),
            )
            if "已添加" in msg:
                self._ok({"message": msg}, msg)
            else:
                self._fail(msg, 400)

        elif route == "/api/import":
            stats, msg = ctl.handle_import(self._resolve_csv(body.get("csv_path", "")))
            if "导入失败" in msg:
                self._fail(msg, 400)
            else:
                self._ok({"stats": asdict(stats), "message": msg}, msg)

        elif route == "/api/rebuild":
            count, msg = ctl.handle_rebuild()
            self._ok({"count": count, "message": msg}, msg)

        elif route == "/api/reminders/check":
            rows, msg = ctl.handle_check_reminders(
                str(body.get("today", "") or ""), int(body.get("lead_days", 7))
            )
            self._ok({"reminders": rows, "message": msg}, msg)

        elif route == "/api/reminders/mark-paid":
            msg = ctl.handle_mark_paid(
                body.get("card_id", ""), body.get("billing_month", "")
            )
            if "已标记" in msg:
                self._ok({"message": msg}, msg)
            else:
                self._fail(msg, 400)

        else:
            self._fail(f"未知路由：POST {route}", 404)


class WebGUIHTTPServer(HTTPServer):
    """单线程 HTTP 服务：SQLite 连接由主线程独占，避免跨线程使用问题。"""

    allow_reuse_address = True

    def __init__(self, address, controller: CardBillController):
        self.controller = controller
        super().__init__(address, WebHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cardbill.webgui",
        description="信用卡账单管理与还款提醒系统（零依赖 Web 图形界面）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1，仅本机）")
    parser.add_argument("--port", type=int, default=8766, help="监听端口（默认 8766）")
    parser.add_argument(
        "--db", default=str(DEFAULT_DB), help=f"SQLite 数据库路径（默认 {DEFAULT_DB}）"
    )
    args = parser.parse_args(argv)

    controller = CardBillController(args.db)
    server = WebGUIHTTPServer((args.host, args.port), controller)
    url = f"http://{args.host}:{args.port}"
    print(f"Web GUI 已启动：{url}")
    print(f"数据库：{controller.db_path}")
    print("在浏览器打开上面的地址即可操作；按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止 Web GUI ...")
    finally:
        server.server_close()
        controller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
