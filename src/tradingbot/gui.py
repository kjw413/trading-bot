from __future__ import annotations

import argparse
import logging
import queue
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from tradingbot.config import PROJECT_ROOT, load_config, resolve_project_path
from tradingbot.report.report import generate_backtest_report
from tradingbot.services import build_cache, build_paper_session, run_backtest, update_data
from tradingbot.strategies.registry import list_strategies
from tradingbot.symbols import SymbolDirectory
from tradingbot.utils.log import setup_logging

MARKETS = ["KR", "US"]
MARKET_HINT = "KR = 한국(코스피/코스닥), US = 미국"
MANUAL_PATH = PROJECT_ROOT / "docs" / "manual.html"

STRATEGY_DESCRIPTIONS = {
    "ma_cross": "이동평균 교차 — 최근 20일 평균 가격이 60일 평균을 위로 뚫으면 사고, 아래로 내려가면 팝니다. 상승 추세를 따라가는 전략입니다.",
    "vol_breakout": "변동성 돌파 — 가격이 '전날 변동폭의 절반'만큼 오르면 사고, 그날 장 마감에 팝니다. 하루 안에 사고파는 단기 전략입니다.",
    "rsi_reversion": "RSI 과매도 반등 — 최근 과하게 떨어진 종목(RSI 30 이하)을 사고, 어느 정도 회복하면 팝니다. 되돌림을 노리는 전략입니다.",
}

WELCOME = (
    "환영합니다! 처음이라면 이렇게 해보세요.\n"
    "  1) [백테스트] 탭의 '종목 선택'에서 종목 이름(예: 삼성전자)으로 검색해 추가하고\n"
    "  2) [백테스트 실행] 버튼을 누르면, 과거 데이터로 전략의 성적표(리포트)가 브라우저에 열립니다.\n"
    "  자세한 내용은 오른쪽 아래 [사용 설명서] 버튼을 눌러보세요."
)


class _QueueLogHandler(logging.Handler):
    """엔진/데이터 계층의 로그를 GUI 로그 창으로 전달한다."""

    def __init__(self, app: TradingBotApp) -> None:
        super().__init__(level=logging.INFO)
        self.app = app
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.app.enqueue_log(self.format(record))
        except Exception:
            pass


class SymbolPicker(ttk.Frame):
    """종목을 이름이나 코드로 검색해 담는 위젯. get_symbols()로 코드 목록을 얻는다."""

    def __init__(self, parent: tk.Misc, app: TradingBotApp, market_var: tk.StringVar) -> None:
        super().__init__(parent)
        self.app = app
        self.market_var = market_var
        self.query_var = tk.StringVar()
        self._results: list[tuple[str, str]] = []
        self._selected: list[tuple[str, str]] = []
        self._searching = False
        market_var.trace_add("write", lambda *_: self._on_market_change())
        self._build()

    def _build(self) -> None:
        search_row = ttk.Frame(self)
        search_row.grid(row=0, column=0, columnspan=4, sticky="we", pady=(0, 4))
        ttk.Label(search_row, text="검색").pack(side="left")
        entry = ttk.Entry(search_row, textvariable=self.query_var, width=24)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda _event: self._start_search())
        self.search_button = ttk.Button(search_row, text="검색", command=self._start_search)
        self.search_button.pack(side="left")
        ttk.Button(search_row, text="코드 직접 추가", command=self._add_direct).pack(side="left", padx=(6, 0))
        ttk.Label(
            search_row, text="이름이나 코드로 검색하세요 (예: 삼성전자, Apple)", foreground="gray"
        ).pack(side="left", padx=(8, 0))

        ttk.Label(self, text="검색 결과 (더블클릭으로 추가)").grid(row=1, column=0, sticky="w")
        ttk.Label(self, text="내가 고른 종목").grid(row=1, column=2, sticky="w")

        self.result_list = tk.Listbox(self, height=6, exportselection=False)
        self.result_list.grid(row=2, column=0, sticky="nswe")
        self.result_list.bind("<Double-Button-1>", lambda _event: self._add_selected_results())

        middle = ttk.Frame(self)
        middle.grid(row=2, column=1, padx=6)
        ttk.Button(middle, text="추가 ▶", command=self._add_selected_results).pack(pady=2)

        self.selected_list = tk.Listbox(self, height=6, exportselection=False)
        self.selected_list.grid(row=2, column=2, sticky="nswe")

        right = ttk.Frame(self)
        right.grid(row=2, column=3, padx=(6, 0), sticky="n")
        ttk.Button(right, text="빼기", command=self._remove_selected).pack(pady=2, fill="x")
        ttk.Button(right, text="모두 빼기", command=self._clear_selected).pack(pady=2, fill="x")

        self.status_label = ttk.Label(self, text="", foreground="gray")
        self.status_label.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))

        self.columnconfigure(0, weight=3)
        self.columnconfigure(2, weight=2)

    # ------------------------------------------------------------------ 조회

    def get_symbols(self) -> list[str]:
        return [code for code, _name in self._selected]

    def get_labels(self) -> list[str]:
        return [self._format(code, name) for code, name in self._selected]

    def set_symbols(self, codes: list[str]) -> None:
        name_map = self.app.name_map(self.market_var.get())
        self._selected = [(code, name_map.get(code, "")) for code in codes]
        self._refresh_selected()

    @staticmethod
    def _format(code: str, name: str) -> str:
        return f"{name} ({code})" if name else code

    # ------------------------------------------------------------------ 검색

    def _start_search(self) -> None:
        query = self.query_var.get().strip()
        if not query:
            messagebox.showinfo("종목 검색", "검색어를 입력하세요. (예: 삼성전자, Apple, 005930)")
            return
        if self._searching:
            return
        market = self.market_var.get()
        self._searching = True
        self.search_button.configure(state="disabled")
        first_download = not self.app.symbol_directory.path(market).exists()
        if first_download:
            self.status_label.configure(text="종목 목록을 처음 내려받는 중입니다... (몇 초 걸릴 수 있어요)")
        else:
            self.status_label.configure(text="검색 중...")

        def worker() -> None:
            try:
                results = self.app.symbol_directory.search(market, query)
                self.app.invalidate_name_map(market)
                self.app.enqueue_call(lambda: self._show_results(results))
            except Exception as exc:
                self.app.enqueue_log(f"종목 검색 실패: {exc} (인터넷 연결을 확인하세요)")
                self.app.enqueue_call(
                    lambda: self.status_label.configure(text="검색 실패 - 인터넷 연결을 확인하세요.")
                )
            finally:
                self.app.enqueue_call(self._end_search)

        threading.Thread(target=worker, daemon=True).start()

    def _end_search(self) -> None:
        self._searching = False
        self.search_button.configure(state="normal")

    def _show_results(self, results: list[tuple[str, str]]) -> None:
        self._results = results
        self.result_list.delete(0, "end")
        for code, name in results:
            self.result_list.insert("end", self._format(code, name))
        if results:
            self.status_label.configure(text=f"{len(results)}개 찾음 - 더블클릭하면 오른쪽 목록에 담깁니다.")
        else:
            self.status_label.configure(
                text="검색 결과가 없습니다. 미국 소형주 등은 [코드 직접 추가]로 티커를 그대로 넣을 수 있어요."
            )

    # ------------------------------------------------------------------ 담기/빼기

    def _add(self, code: str, name: str) -> None:
        if any(existing == code for existing, _ in self._selected):
            return
        self._selected.append((code, name))

    def _add_selected_results(self) -> None:
        indexes = self.result_list.curselection()
        if not indexes:
            messagebox.showinfo("종목 추가", "검색 결과에서 종목을 먼저 클릭하세요.")
            return
        for index in indexes:
            code, name = self._results[index]
            self._add(code, name)
        self._refresh_selected()

    def _add_direct(self) -> None:
        code = self.query_var.get().strip().upper()
        if not code:
            messagebox.showinfo("코드 직접 추가", "추가할 종목 코드를 검색란에 입력하세요. (예: 005930, TSLA)")
            return
        name_map = self.app.name_map(self.market_var.get())
        self._add(code, name_map.get(code, ""))
        self._refresh_selected()

    def _remove_selected(self) -> None:
        indexes = self.selected_list.curselection()
        if not indexes:
            messagebox.showinfo("종목 빼기", "오른쪽 목록에서 뺄 종목을 먼저 클릭하세요.")
            return
        for index in sorted(indexes, reverse=True):
            del self._selected[index]
        self._refresh_selected()

    def _clear_selected(self) -> None:
        self._selected = []
        self._refresh_selected()

    def _refresh_selected(self) -> None:
        self.selected_list.delete(0, "end")
        for code, name in self._selected:
            self.selected_list.insert("end", self._format(code, name))

    def _on_market_change(self) -> None:
        self._results = []
        self._selected = []
        self.result_list.delete(0, "end")
        self.selected_list.delete(0, "end")
        self.status_label.configure(text="")


class TradingBotApp:
    def __init__(self, root: tk.Tk, config_path: str | None = None) -> None:
        self.root = root
        self.root.title("Trading Bot")
        self.root.geometry("1000x820")
        self.root.minsize(860, 700)

        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._task_running = False
        self._paper_stop = threading.Event()
        self._paper_thread: threading.Thread | None = None
        self._action_buttons: list[ttk.Button] = []
        self._last_report_path: Path | None = None
        self._name_maps: dict[str, dict[str, str]] = {}

        self.config_path_var = tk.StringVar(value=config_path or "")
        self.symbol_directory = SymbolDirectory(self._initial_cache_dir(config_path))

        self._build_menu()
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._process_queue)
        self.enqueue_log(WELCOME)

    @staticmethod
    def _initial_cache_dir(config_path: str | None) -> Path:
        try:
            config = load_config(config_path or None)
            return resolve_project_path(config["data"]["cache_dir"])
        except Exception:
            return resolve_project_path("data/cache")

    # ------------------------------------------------------------------ 레이아웃

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="사용 설명서 열기", command=self._open_manual)
        menubar.add_cascade(label="도움말", menu=help_menu)
        self.root.config(menu=menubar)

    def _build_layout(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 8, 10, 0))
        top.pack(fill="x")
        ttk.Label(top, text="설정 파일").pack(side="left")
        ttk.Entry(top, textvariable=self.config_path_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="찾아보기", command=self._browse_config).pack(side="left")
        ttk.Label(top, text="(비우면 기본 설정 사용)").pack(side="left", padx=(6, 0))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="x", padx=10, pady=8)
        notebook.add(self._build_backtest_tab(notebook), text="백테스트 (과거 검증)")
        notebook.add(self._build_paper_tab(notebook), text="모의투자 (가상 돈)")
        notebook.add(self._build_data_tab(notebook), text="데이터 받기")

        log_frame = ttk.LabelFrame(self.root, text="진행 상황", padding=6)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self.log_text = ScrolledText(log_frame, height=12, state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)

        status_bar = ttk.Frame(self.root, padding=(10, 2, 10, 6))
        status_bar.pack(fill="x")
        self.status_var = tk.StringVar(value="대기 중")
        ttk.Label(status_bar, textvariable=self.status_var).pack(side="left")
        ttk.Button(status_bar, text="사용 설명서", command=self._open_manual).pack(side="right")
        ttk.Button(status_bar, text="로그 지우기", command=self._clear_log).pack(side="right", padx=(0, 6))

    def _build_form(self, parent: ttk.Frame, fields: list[tuple[str, tk.Variable, dict]], start_row: int = 0) -> int:
        row = start_row
        for label, var, options in fields:
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            if options.get("choices"):
                widget = ttk.Combobox(
                    parent, textvariable=var, values=options["choices"], state="readonly", width=28
                )
            else:
                widget = ttk.Entry(parent, textvariable=var, width=30)
            widget.grid(row=row, column=1, sticky="we", pady=3)
            if options.get("hint"):
                ttk.Label(parent, text=options["hint"], foreground="gray").grid(
                    row=row, column=2, sticky="w", padx=(8, 0)
                )
            row += 1
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=1)
        return row

    def _add_symbol_picker(self, tab: ttk.Frame, market_var: tk.StringVar, row: int) -> tuple[SymbolPicker, int]:
        frame = ttk.LabelFrame(tab, text="종목 선택", padding=6)
        frame.grid(row=row, column=0, columnspan=3, sticky="we", pady=4)
        picker = SymbolPicker(frame, self, market_var)
        picker.pack(fill="x")
        return picker, row + 1

    def _add_strategy_desc(self, tab: ttk.Frame, strategy_var: tk.StringVar, row: int) -> int:
        label = ttk.Label(tab, text="", foreground="gray", wraplength=680, justify="left")
        label.grid(row=row, column=1, columnspan=2, sticky="w", pady=(0, 4))

        def update(*_args) -> None:
            label.configure(text=STRATEGY_DESCRIPTIONS.get(strategy_var.get(), ""))

        strategy_var.trace_add("write", update)
        update()
        return row + 1

    def _build_backtest_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=10)
        strategies = list_strategies()
        self.bt_market = tk.StringVar(value="KR")
        self.bt_strategy = tk.StringVar(value=strategies[0] if strategies else "")
        self.bt_start = tk.StringVar(value="2020-01-01")
        self.bt_end = tk.StringVar(value="")
        self.bt_auto_update = tk.BooleanVar(value=True)
        self.bt_report = tk.BooleanVar(value=True)

        row = self._build_form(tab, [("시장", self.bt_market, {"choices": MARKETS, "hint": MARKET_HINT})])
        self.bt_symbols, row = self._add_symbol_picker(tab, self.bt_market, row)
        row = self._build_form(
            tab,
            [
                ("전략", self.bt_strategy, {"choices": strategies, "hint": "매매 규칙"}),
            ],
            start_row=row,
        )
        row = self._add_strategy_desc(tab, self.bt_strategy, row)
        row = self._build_form(
            tab,
            [
                ("시작일", self.bt_start, {"hint": "이 날짜부터의 과거 데이터로 검증 (YYYY-MM-DD)"}),
                ("종료일", self.bt_end, {"hint": "비우면 최근일까지"}),
            ],
            start_row=row,
        )
        ttk.Checkbutton(tab, text="실행 전 시세 데이터 자동 받기 (인터넷 필요, 권장)", variable=self.bt_auto_update).grid(
            row=row, column=1, columnspan=2, sticky="w"
        )
        row += 1
        ttk.Checkbutton(tab, text="끝나면 결과 리포트를 브라우저로 열기", variable=self.bt_report).grid(
            row=row, column=1, columnspan=2, sticky="w"
        )
        row += 1

        button_row = ttk.Frame(tab)
        button_row.grid(row=row, column=1, sticky="w", pady=(10, 0))
        run_button = ttk.Button(button_row, text="백테스트 실행", command=self._start_backtest)
        run_button.pack(side="left")
        self._action_buttons.append(run_button)
        self.open_report_button = ttk.Button(
            button_row, text="지난 리포트 다시 열기", command=self._open_report, state="disabled"
        )
        self.open_report_button.pack(side="left", padx=(8, 0))
        return tab

    def _build_paper_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=10)
        strategies = list_strategies()
        self.pp_name = tk.StringVar(value="paper1")
        self.pp_market = tk.StringVar(value="KR")
        self.pp_strategy = tk.StringVar(value=strategies[0] if strategies else "")
        self.pp_start = tk.StringVar(value="2020-01-01")
        self.pp_sleep = tk.StringVar(value="")
        self.pp_auto_update = tk.BooleanVar(value=True)

        ttk.Label(
            tab,
            text="모의투자는 가상의 돈으로 전략을 실제 시세에 따라 굴려보는 기능입니다. 실제 주문은 나가지 않습니다.",
            foreground="gray",
            wraplength=760,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        row = self._build_form(
            tab,
            [
                ("계좌 이름", self.pp_name, {"hint": "가상 계좌 저장 이름 (자유롭게)"}),
                ("시장", self.pp_market, {"choices": MARKETS, "hint": MARKET_HINT}),
            ],
            start_row=1,
        )
        self.pp_symbols, row = self._add_symbol_picker(tab, self.pp_market, row)
        row = self._build_form(
            tab,
            [
                ("전략", self.pp_strategy, {"choices": strategies, "hint": "매매 규칙"}),
            ],
            start_row=row,
        )
        row = self._add_strategy_desc(tab, self.pp_strategy, row)
        row = self._build_form(
            tab,
            [
                ("웜업 시작일", self.pp_start, {"hint": "전략 계산에 쓸 과거 데이터 시작일 (그대로 둬도 됨)"}),
                ("확인 간격(초)", self.pp_sleep, {"hint": "루프에서 시세를 다시 보는 간격, 비우면 기본값(300초)"}),
            ],
            start_row=row,
        )
        ttk.Checkbutton(tab, text="실행 전 시세 데이터 자동 받기 (인터넷 필요, 권장)", variable=self.pp_auto_update).grid(
            row=row, column=1, columnspan=2, sticky="w"
        )
        row += 1

        button_row = ttk.Frame(tab)
        button_row.grid(row=row, column=1, sticky="w", pady=(10, 0))
        once_button = ttk.Button(button_row, text="지금 한 번 실행", command=self._start_paper_once)
        once_button.pack(side="left")
        loop_button = ttk.Button(button_row, text="자동 반복 시작", command=self._start_paper_loop)
        loop_button.pack(side="left", padx=(8, 0))
        self._action_buttons.extend([once_button, loop_button])
        self.paper_stop_button = ttk.Button(
            button_row, text="자동 반복 중지", command=self._stop_paper_loop, state="disabled"
        )
        self.paper_stop_button.pack(side="left", padx=(8, 0))
        return tab

    def _build_data_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=10)
        self.dt_market = tk.StringVar(value="KR")
        self.dt_start = tk.StringVar(value="2020-01-01")
        self.dt_end = tk.StringVar(value="")

        ttk.Label(
            tab,
            text="종목의 과거 시세(일봉)를 내 컴퓨터에 저장해 두는 기능입니다. 백테스트/모의투자 탭에서 '자동 받기'를 켜두면 따로 할 필요는 없습니다.",
            foreground="gray",
            wraplength=760,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        row = self._build_form(
            tab,
            [("시장", self.dt_market, {"choices": MARKETS, "hint": MARKET_HINT})],
            start_row=1,
        )
        self.dt_symbols, row = self._add_symbol_picker(tab, self.dt_market, row)
        row = self._build_form(
            tab,
            [
                ("시작일", self.dt_start, {"hint": "비우면 저장된 데이터 이후만 받음"}),
                ("종료일", self.dt_end, {"hint": "비우면 최근일까지"}),
            ],
            start_row=row,
        )
        update_button = ttk.Button(tab, text="데이터 받기", command=self._start_data_update)
        update_button.grid(row=row, column=1, sticky="w", pady=(10, 0))
        self._action_buttons.append(update_button)
        return tab

    # ------------------------------------------------------------------ 공통 유틸

    def _browse_config(self) -> None:
        path = filedialog.askopenfilename(
            title="설정 파일 선택", filetypes=[("TOML", "*.toml"), ("모든 파일", "*.*")]
        )
        if path:
            self.config_path_var.set(path)

    def _load_config(self) -> dict:
        path = self.config_path_var.get().strip() or None
        return load_config(path)

    def _open_manual(self) -> None:
        if MANUAL_PATH.exists():
            webbrowser.open(MANUAL_PATH.resolve().as_uri())
        else:
            messagebox.showwarning("사용 설명서", f"설명서 파일을 찾을 수 없습니다:\n{MANUAL_PATH}")

    def name_map(self, market: str) -> dict[str, str]:
        """캐시된 종목 목록의 코드 -> 이름 매핑 (네트워크 사용 없음)."""
        if market not in self._name_maps:
            try:
                self._name_maps[market] = self.symbol_directory.name_map(market)
            except Exception:
                self._name_maps[market] = {}
        return self._name_maps[market]

    def invalidate_name_map(self, market: str) -> None:
        self._name_maps.pop(market, None)

    def display_symbol(self, market: str, code: str) -> str:
        name = self.name_map(market).get(code)
        return f"{name}({code})" if name else code

    def enqueue_log(self, text: str) -> None:
        self._queue.put(("log", text))

    def enqueue_call(self, fn) -> None:
        self._queue.put(("call", fn))

    def _process_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "call":
                    payload()
        except queue.Empty:
            pass
        self.root.after(100, self._process_queue)

    def _append_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {text}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool, status: str) -> None:
        self._task_running = running
        state = "disabled" if running else "normal"
        for button in self._action_buttons:
            button.configure(state=state)
        self.status_var.set(status)

    def _run_task(self, status: str, fn) -> None:
        """fn을 워커 스레드에서 실행한다. 한 번에 하나의 작업만 허용."""
        if self._task_running:
            messagebox.showinfo("실행 중", "이미 작업이 실행 중입니다. 끝날 때까지 기다려주세요.")
            return
        self._set_running(True, status)

        def worker() -> None:
            try:
                fn()
            except Exception as exc:
                self.enqueue_log(f"문제가 생겼습니다: {exc}")
                self.enqueue_log(traceback.format_exc().rstrip())
            finally:
                self.enqueue_call(lambda: self._set_running(False, "대기 중"))

        threading.Thread(target=worker, daemon=True).start()

    def _auto_update_data(
        self, config: dict, market: str, symbols: list[str], start: str | None, end: str | None
    ) -> None:
        """작업 전에 시세 데이터를 받는다. 실패해도 캐시가 있으면 계속 진행한다."""
        self.enqueue_log("시세 데이터를 받는 중... (인터넷 사용)")
        try:
            for result in update_data(config, market=market, symbols=symbols, start=start, end=end):
                self.enqueue_log(f"  {self.display_symbol(market, result.symbol)}: {result.rows}일치 저장됨")
        except Exception as exc:
            cache = build_cache(config)
            missing = [s for s in symbols if not cache.path(market, s).exists()]
            if missing:
                labels = ", ".join(self.display_symbol(market, s) for s in missing)
                raise RuntimeError(
                    f"시세 데이터를 받지 못했습니다: {labels}. 인터넷 연결과 종목 코드를 확인하세요."
                ) from exc
            self.enqueue_log(f"데이터 받기 실패 - 이전에 받아둔 데이터로 계속합니다. ({exc})")

    # ------------------------------------------------------------------ 백테스트

    def _start_backtest(self) -> None:
        symbols = self.bt_symbols.get_symbols()
        labels = self.bt_symbols.get_labels()
        strategy = self.bt_strategy.get().strip()
        start = self.bt_start.get().strip()
        if not symbols:
            messagebox.showwarning("입력 확인", "'종목 선택'에서 종목을 검색해 먼저 추가하세요.")
            return
        if not strategy or not start:
            messagebox.showwarning("입력 확인", "전략과 시작일을 입력하세요.")
            return
        market = self.bt_market.get()
        end = self.bt_end.get().strip() or None
        auto_update = self.bt_auto_update.get()
        make_report = self.bt_report.get()
        self.open_report_button.configure(state="disabled")
        self._last_report_path = None

        def task() -> None:
            self.enqueue_log(f"백테스트 시작: {strategy} / {market} / {', '.join(labels)}")
            config = self._load_config()
            if auto_update:
                self._auto_update_data(config, market, symbols, start, end)
            result = run_backtest(
                config,
                market=market,
                symbols=symbols,
                strategy_name=strategy,
                start=start,
                end=end,
            )
            self.enqueue_log(f"최종 자산: {result.final_equity:,.2f}")
            self.enqueue_log(f"수익률: {result.return_pct:,.2f}%")
            self.enqueue_log(f"체결수: {result.trade_count}")
            self.enqueue_log(f"거부 주문: {len(result.rejected_orders)}")
            for reason, count in Counter(
                order.reject_reason or "unknown" for order in result.rejected_orders
            ).items():
                self.enqueue_log(f"  - {reason}: {count}")
            self.enqueue_log(f"만료 주문: {len(result.expired_orders)}")

            if make_report:
                report_path = generate_backtest_report(
                    result,
                    strategy_name=strategy,
                    market=market,
                    symbols=symbols,
                    reports_root=resolve_project_path("reports"),
                )
                self._last_report_path = report_path
                self.enqueue_log(f"리포트 저장: {report_path}")
                self.enqueue_call(lambda: self.open_report_button.configure(state="normal"))
                webbrowser.open(report_path.resolve().as_uri())
                self.enqueue_log("브라우저에서 결과 리포트를 열었습니다.")
            self.enqueue_log("백테스트 완료")

        self._run_task("백테스트 실행 중...", task)

    def _open_report(self) -> None:
        if self._last_report_path is None:
            return
        webbrowser.open(self._last_report_path.resolve().as_uri())

    # ------------------------------------------------------------------ 모의투자

    def _paper_inputs(self) -> dict | None:
        symbols = self.pp_symbols.get_symbols()
        labels = self.pp_symbols.get_labels()
        name = self.pp_name.get().strip()
        strategy = self.pp_strategy.get().strip()
        start = self.pp_start.get().strip()
        if not symbols:
            messagebox.showwarning("입력 확인", "'종목 선택'에서 종목을 검색해 먼저 추가하세요.")
            return None
        if not name or not strategy or not start:
            messagebox.showwarning("입력 확인", "계좌 이름, 전략, 웜업 시작일을 입력하세요.")
            return None
        sleep_raw = self.pp_sleep.get().strip()
        try:
            sleep_seconds = int(sleep_raw) if sleep_raw else None
        except ValueError:
            messagebox.showwarning("입력 확인", "확인 간격은 숫자(초)로 입력하세요.")
            return None
        return {
            "name": name,
            "market": self.pp_market.get(),
            "symbols": symbols,
            "labels": labels,
            "strategy_name": strategy,
            "start": start,
            "sleep_seconds": sleep_seconds,
            "auto_update": self.pp_auto_update.get(),
        }

    def _log_paper_snapshot(self, market: str, snapshot: dict[str, object]) -> None:
        actions = snapshot.get("actions", [])
        action_text = ", ".join(str(action) for action in actions) if actions else "변화 없음"
        self.enqueue_log(
            f"[{snapshot['now']}] 동작={action_text} "
            f"현금={snapshot['cash']:,.2f} 평가자산={snapshot['equity']:,.2f} "
            f"미체결 주문={snapshot['open_orders']}"
        )
        positions = snapshot.get("positions", {})
        if isinstance(positions, dict) and positions:
            position_text = ", ".join(
                f"{self.display_symbol(market, symbol)} {qty}주" for symbol, qty in sorted(positions.items())
            )
            self.enqueue_log(f"보유 종목: {position_text}")

    def _build_session(self, config: dict, inputs: dict):
        if inputs["auto_update"]:
            self._auto_update_data(config, inputs["market"], inputs["symbols"], inputs["start"], None)
        return build_paper_session(
            config,
            name=inputs["name"],
            market=inputs["market"],
            symbols=inputs["symbols"],
            strategy_name=inputs["strategy_name"],
            start=inputs["start"],
        )

    def _start_paper_once(self) -> None:
        inputs = self._paper_inputs()
        if inputs is None:
            return

        def task() -> None:
            self.enqueue_log(f"모의투자 1회 실행: {inputs['name']} / {', '.join(inputs['labels'])}")
            config = self._load_config()
            session = self._build_session(config, inputs)
            self.enqueue_log(f"가상 계좌 저장 위치: {session.broker.state_path}")
            snapshot = session.engine.run_once()
            self._log_paper_snapshot(inputs["market"], snapshot)
            self.enqueue_log("모의투자 1회 실행 완료 (장이 닫혀 있으면 '변화 없음'이 정상입니다)")

        self._run_task("모의투자 실행 중...", task)

    def _start_paper_loop(self) -> None:
        inputs = self._paper_inputs()
        if inputs is None:
            return
        if self._task_running:
            messagebox.showinfo("실행 중", "이미 작업이 실행 중입니다. 끝날 때까지 기다려주세요.")
            return

        self._paper_stop.clear()
        self._set_running(True, f"모의투자 자동 반복 중: {inputs['name']}")
        self.paper_stop_button.configure(state="normal")

        def worker() -> None:
            try:
                config = self._load_config()
                session = self._build_session(config, inputs)
                sleep_seconds = inputs["sleep_seconds"] or session.poll_interval_seconds
                self.enqueue_log(
                    f"모의투자 자동 반복 시작: {inputs['name']} / {', '.join(inputs['labels'])} "
                    f"({sleep_seconds}초마다 확인)"
                )
                self.enqueue_log(f"가상 계좌 저장 위치: {session.broker.state_path}")
                while not self._paper_stop.is_set():
                    try:
                        snapshot = session.engine.run_once()
                        self._log_paper_snapshot(inputs["market"], snapshot)
                    except Exception as exc:
                        self.enqueue_log(f"모의투자 반복 중 오류 (계속 진행): {exc}")
                    self._paper_stop.wait(sleep_seconds)
                self.enqueue_log("모의투자 자동 반복을 중지했습니다.")
            except Exception as exc:
                self.enqueue_log(f"문제가 생겼습니다: {exc}")
                self.enqueue_log(traceback.format_exc().rstrip())
            finally:
                self.enqueue_call(self._on_paper_loop_finished)

        self._paper_thread = threading.Thread(target=worker, daemon=True)
        self._paper_thread.start()

    def _on_paper_loop_finished(self) -> None:
        self.paper_stop_button.configure(state="disabled")
        self._set_running(False, "대기 중")

    def _stop_paper_loop(self) -> None:
        self._paper_stop.set()
        self.status_var.set("모의투자 자동 반복 중지 중...")

    # ------------------------------------------------------------------ 데이터

    def _start_data_update(self) -> None:
        symbols = self.dt_symbols.get_symbols()
        labels = self.dt_symbols.get_labels()
        if not symbols:
            messagebox.showwarning("입력 확인", "'종목 선택'에서 종목을 검색해 먼저 추가하세요.")
            return
        market = self.dt_market.get()
        start = self.dt_start.get().strip() or None
        end = self.dt_end.get().strip() or None

        def task() -> None:
            self.enqueue_log(f"데이터 받기 시작: {market} / {', '.join(labels)}")
            config = self._load_config()
            for result in update_data(config, market=market, symbols=symbols, start=start, end=end):
                self.enqueue_log(f"  {self.display_symbol(market, result.symbol)}: {result.rows}일치 저장됨")
            self.enqueue_log("데이터 받기 완료")

        self._run_task("데이터 받는 중...", task)

    # ------------------------------------------------------------------ 종료

    def _on_close(self) -> None:
        self._paper_stop.set()
        thread = self._paper_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self.root.destroy()


def run_gui(config_path: str | None = None) -> int:
    setup_logging()
    root = tk.Tk()
    app = TradingBotApp(root, config_path=config_path)
    handler = _QueueLogHandler(app)
    logging.getLogger().addHandler(handler)
    try:
        root.mainloop()
    finally:
        logging.getLogger().removeHandler(handler)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradingbot-gui")
    parser.add_argument("--config", default=None, help="TOML config path")
    args = parser.parse_args(argv)
    return run_gui(config_path=args.config)


if __name__ == "__main__":
    raise SystemExit(main())
