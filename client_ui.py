"""
五子棋联机 — GUI 渲染模块。所有 tkinter 操作集中在此，不含游戏逻辑与网络通信。
"""

import time
import tkinter as tk

# ── UI 可调常量 ──────────────────────────────────────
CELL_SIZE = 40
PADDING = 30
PIECE_RADIUS = 17
TURN_TIME_LIMIT = 30
BOARD_SIZE = 15
BLACK = 1
WHITE = 2


class GameUI:
    """游戏界面管理器，负责所有 tkinter 组件与渲染。"""

    def __init__(self, root: tk.Tk, callbacks: dict):
        """
        callbacks 字典必须包含：
          on_connect(ip, port, nick)   — 连接按钮点击
          on_click(row, col)           — 棋盘点击
          on_undo()                    — 悔棋
          on_resign()                  — 认输确认
          on_rematch_yes()             — 再来一局
          on_rematch_no()              — 离开
          on_reconnect()               — 手动重连
          on_time_change(seconds)      — 限时变更
          on_size_change(cell, radius) — 棋盘尺寸变更
        """
        self.root = root
        self.cb = callbacks

        # 控件引用（在 show_xxx_screen 中赋值）
        self._connect_status: tk.Label | None = None
        self.ip_entry: tk.Entry | None = None
        self.port_entry: tk.Entry | None = None
        self.nick_entry: tk.Entry | None = None

        self.players_label: tk.Label | None = None
        self.status_label: tk.Label | None = None
        self.timer_label: tk.Label | None = None
        self.reconnect_btn: tk.Button | None = None
        self._note_label: tk.Label | None = None
        self.canvas: tk.Canvas | None = None
        self.move_listbox: tk.Listbox | None = None

        # 倒计时
        self._countdown_value = TURN_TIME_LIMIT
        self._countdown_job = None
        self.countdown_running = False

        # 通知
        self._note_job = None

        # 庆祝动画
        self._celebration_job = None
        self._celebration_dots: list[dict] = []

    # ═══════════════════════════════════════════════════
    #  连接界面
    # ═══════════════════════════════════════════════════
    def _clear_root(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.canvas = None

    def show_connect_screen(self):
        self._clear_root()
        self.root.geometry("360x260")

        frame = tk.Frame(self.root)
        frame.pack(expand=True)

        tk.Label(frame, text="五子棋联机对战", font=("", 18, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 15))

        tk.Label(frame, text="IP 地址:").grid(row=1, column=0, sticky="e", padx=5, pady=4)
        self.ip_entry = tk.Entry(frame, width=20)
        self.ip_entry.insert(0, "127.0.0.1")
        self.ip_entry.grid(row=1, column=1, sticky="w", pady=4)

        tk.Label(frame, text="端口:").grid(row=2, column=0, sticky="e", padx=5, pady=4)
        self.port_entry = tk.Entry(frame, width=20)
        self.port_entry.insert(0, "9527")
        self.port_entry.grid(row=2, column=1, sticky="w", pady=4)

        tk.Label(frame, text="昵称:").grid(row=3, column=0, sticky="e", padx=5, pady=4)
        self.nick_entry = tk.Entry(frame, width=20)
        self.nick_entry.grid(row=3, column=1, sticky="w", pady=4)

        tk.Button(frame, text="连接服务器", command=self._on_connect_btn,
                  width=16, bg="#4CAF50", fg="white").grid(
            row=4, column=0, columnspan=2, pady=(15, 0))

        self._connect_status = tk.Label(frame, text="", fg="gray")
        self._connect_status.grid(row=5, column=0, columnspan=2)

        self.root.bind("<Return>", lambda e: self._on_connect_btn())

    def set_connect_status(self, text: str, color: str):
        if self._connect_status:
            self._connect_status.config(text=text, fg=color)

    def _on_connect_btn(self):
        ip = self.ip_entry.get().strip() if self.ip_entry else ""
        try:
            port = int(self.port_entry.get().strip()) if self.port_entry else 0
        except ValueError:
            self.set_connect_status("端口必须为整数", "red")
            return
        nickname = self.nick_entry.get().strip() if self.nick_entry else ""
        if not nickname:
            self.set_connect_status("请输入昵称", "red")
            return
        self.set_connect_status("正在连接...", "blue")
        self.root.update()
        self.cb['on_connect'](ip, port, nickname)

    # ═══════════════════════════════════════════════════
    #  游戏界面
    # ═══════════════════════════════════════════════════
    def show_game_screen(self):
        self._clear_root()
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        self.root.geometry(f"{board_px + 220}x{board_px + 60}")

        info_frame = tk.Frame(self.root)
        info_frame.pack(side="top", fill="x", padx=10, pady=5)

        self.players_label = tk.Label(info_frame, text="", font=("", 11, "bold"), fg="#333")
        self.players_label.pack(side="left")

        self.status_label = tk.Label(info_frame, text="等待游戏开始...", font=("", 10), fg="#555")
        self.status_label.pack(side="left", padx=20)

        self.timer_label = tk.Label(info_frame, text="", font=("", 12, "bold"), fg="#D32F2F")
        self.timer_label.pack(side="right")

        self.reconnect_btn = tk.Button(
            info_frame, text="手动重连", command=self.cb['on_reconnect'],
            bg="#2196F3", fg="white")

        main_frame = tk.Frame(self.root)
        main_frame.pack(side="top")

        self._note_label = tk.Label(main_frame, text="", font=("", 10, "bold"),
                                    fg="white", bg="#333", padx=15, pady=4,
                                    wraplength=board_px + 200)
        self._note_job = None

        self.canvas = tk.Canvas(main_frame, width=board_px, height=board_px,
                                bg="#DEB887", cursor="hand2")
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self._on_canvas_click)

        panel = tk.Frame(main_frame, width=200)
        panel.pack(side="right", fill="y", padx=(10, 0))

        tk.Label(panel, text="操作", font=("", 11, "bold")).pack(pady=(0, 10))

        tk.Button(panel, text="悔棋 (Ctrl+R)", command=self.cb['on_undo'],
                  width=16, bg="#FF9800", fg="white").pack(pady=3)
        tk.Button(panel, text="认输 (Ctrl+G)", command=self.cb['on_resign'],
                  width=16, bg="#f44336", fg="white").pack(pady=3)

        tk.Label(panel, text="").pack()

        tk.Label(panel, text="棋盘/棋子大小", font=("", 9)).pack()
        size_frame = tk.Frame(panel)
        size_frame.pack(pady=3)
        tk.Button(size_frame, text="小",
                  command=lambda: self.cb['on_size_change'](30, 13), width=4).pack(side="left", padx=2)
        tk.Button(size_frame, text="中",
                  command=lambda: self.cb['on_size_change'](40, 17), width=4).pack(side="left", padx=2)
        tk.Button(size_frame, text="大",
                  command=lambda: self.cb['on_size_change'](50, 22), width=4).pack(side="left", padx=2)

        tk.Label(panel, text="限时时长", font=("", 9)).pack(pady=(10, 0))
        time_frame = tk.Frame(panel)
        time_frame.pack(pady=3)
        tk.Button(time_frame, text="30s",
                  command=lambda: self.cb['on_time_change'](30), width=4).pack(side="left", padx=2)
        tk.Button(time_frame, text="60s",
                  command=lambda: self.cb['on_time_change'](60), width=4).pack(side="left", padx=2)
        tk.Button(time_frame, text="90s",
                  command=lambda: self.cb['on_time_change'](90), width=4).pack(side="left", padx=2)

        tk.Label(panel, text="落子记录", font=("", 9)).pack(pady=(10, 0))
        self.move_listbox = tk.Listbox(panel, height=14, width=22, font=("", 8))
        self.move_listbox.pack(pady=3, fill="both", expand=True)

        self.root.bind("<Control-r>", lambda e: self.cb['on_undo']())
        self.root.bind("<Control-R>", lambda e: self.cb['on_undo']())
        self.root.bind("<Control-g>", lambda e: self.cb['on_resign']())
        self.root.bind("<Control-G>", lambda e: self.cb['on_resign']())

    # ═══════════════════════════════════════════════════
    #  棋盘绘制
    # ═══════════════════════════════════════════════════
    def draw_board(self, board: list[list[int]]):
        if not self.canvas:
            return
        self.canvas.delete("board")
        board_width = (BOARD_SIZE - 1) * CELL_SIZE

        for i in range(BOARD_SIZE):
            coord = PADDING + i * CELL_SIZE
            self.canvas.create_line(PADDING, coord, PADDING + board_width, coord,
                                    fill="#555", tags="board")
            self.canvas.create_line(coord, PADDING, coord, PADDING + board_width,
                                    fill="#555", tags="board")

        star_points = [(3, 3), (3, 7), (3, 11), (7, 3), (7, 7), (7, 11),
                       (11, 3), (11, 7), (11, 11)]
        for r, c in star_points:
            cx = PADDING + c * CELL_SIZE
            cy = PADDING + r * CELL_SIZE
            self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                                    fill="#333", outline="", tags="board")

        for r in range(BOARD_SIZE):
            for c in range(BOARD_SIZE):
                if board[r][c] != 0:
                    self._draw_piece(r, c, board[r][c])

    def _draw_piece(self, row: int, col: int, color: int):
        if not self.canvas:
            return
        cx = PADDING + col * CELL_SIZE
        cy = PADDING + row * CELL_SIZE
        fill = "#1a1a1a" if color == BLACK else "#FAFAFA"
        outline = "#555" if color == BLACK else "#333"
        self.canvas.create_oval(
            cx - PIECE_RADIUS, cy - PIECE_RADIUS,
            cx + PIECE_RADIUS, cy + PIECE_RADIUS,
            fill=fill, outline=outline, width=2, tags="board"
        )

    # ═══════════════════════════════════════════════════
    #  棋盘点击
    # ═══════════════════════════════════════════════════
    def _on_canvas_click(self, event):
        overlapping = self.canvas.find_overlapping(
            event.x - 1, event.y - 1, event.x + 1, event.y + 1)
        for item in overlapping:
            tags = self.canvas.gettags(item)
            if "confirm_overlay" in tags or "overlay" in tags:
                return
        col = round((event.x - PADDING) / CELL_SIZE)
        row = round((event.y - PADDING) / CELL_SIZE)
        self.cb['on_click'](row, col)

    # ═══════════════════════════════════════════════════
    #  确认遮罩（认输）
    # ═══════════════════════════════════════════════════
    def show_confirm_overlay(self):
        if not self.canvas:
            return
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        bar_top = board_px // 2 - 35

        self.canvas.create_rectangle(0, 0, board_px, board_px,
                                     fill="", outline="", tags="confirm_overlay")
        self.canvas.create_rectangle(20, bar_top, board_px - 20, bar_top + 70,
                                     fill="#2d2d2d", outline="#555555",
                                     tags="confirm_overlay")
        self.canvas.create_text(board_px // 2, bar_top + 22,
                                text="确定要认输吗？", font=("", 14, "bold"),
                                fill="#FFD700", tags="confirm_overlay")
        self.canvas.create_rectangle(
            board_px // 2 - 90, bar_top + 40, board_px // 2 - 10, bar_top + 58,
            fill="#f44336", outline="",
            tags=("confirm_overlay", "confirm_yes_btn"))
        self.canvas.create_text(board_px // 2 - 50, bar_top + 49,
                                text="确认认输", font=("", 10, "bold"),
                                fill="white",
                                tags=("confirm_overlay", "confirm_yes_btn"))
        self.canvas.create_rectangle(
            board_px // 2 + 10, bar_top + 40, board_px // 2 + 90, bar_top + 58,
            fill="#888", outline="",
            tags=("confirm_overlay", "confirm_no_btn"))
        self.canvas.create_text(board_px // 2 + 50, bar_top + 49,
                                text="取消", font=("", 10, "bold"),
                                fill="white",
                                tags=("confirm_overlay", "confirm_no_btn"))
        self.canvas.tag_bind("confirm_yes_btn", "<Button-1>",
                             lambda e: self._on_confirm_yes())
        self.canvas.tag_bind("confirm_no_btn", "<Button-1>",
                             lambda e: self.hide_confirm_overlay())

    def _on_confirm_yes(self):
        self.hide_confirm_overlay()
        self.cb['on_resign_confirm']()

    def hide_confirm_overlay(self):
        if self.canvas:
            self.canvas.delete("confirm_overlay")

    # ═══════════════════════════════════════════════════
    #  胜利结算遮罩
    # ═══════════════════════════════════════════════════
    def show_game_over_overlay(self, result: str):
        if not self.canvas:
            return
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2

        self.canvas.create_rectangle(0, 0, board_px, board_px,
                                     fill="", outline="", tags="overlay")
        bar_top = board_px // 2 - 55
        self.canvas.create_rectangle(20, bar_top, board_px - 20, bar_top + 110,
                                     fill="#2d2d2d", outline="#555555",
                                     tags="overlay")

        if "黑" in result:
            emoji = "🖤"
        elif "白" in result:
            emoji = "🤍"
        else:
            emoji = "🤝"
        self.canvas.create_text(board_px // 2, bar_top + 35,
                                text=f"{emoji}  {result}  {emoji}",
                                font=("", 20, "bold"), fill="#FFD700",
                                tags="overlay")

        btn_y = bar_top + 72
        self.canvas.create_rectangle(
            board_px // 2 - 90, btn_y - 13, board_px // 2 - 10, btn_y + 13,
            fill="#4CAF50", outline="",
            tags=("overlay", "rematch_yes_btn"))
        self.canvas.create_text(board_px // 2 - 50, btn_y,
                                text="再来一局", font=("", 11, "bold"),
                                fill="white", tags=("overlay", "rematch_yes_btn"))
        self.canvas.create_rectangle(
            board_px // 2 + 10, btn_y - 13, board_px // 2 + 90, btn_y + 13,
            fill="#888", outline="",
            tags=("overlay", "rematch_no_btn"))
        self.canvas.create_text(board_px // 2 + 50, btn_y,
                                text="离开", font=("", 11, "bold"),
                                fill="white", tags=("overlay", "rematch_no_btn"))

        self.canvas.tag_bind("rematch_yes_btn", "<Button-1>",
                             lambda e: self.cb['on_rematch_yes']())
        self.canvas.tag_bind("rematch_no_btn", "<Button-1>",
                             lambda e: self.cb['on_rematch_no']())

        self.canvas.create_text(board_px // 2, btn_y + 28,
                                text="", font=("", 9), fill="#CCC",
                                tags=("overlay", "rematch_status_text"))

        self._start_celebration()

    def hide_game_over_overlay(self):
        self._stop_celebration()
        if self.canvas:
            self.canvas.delete("overlay")
            self.canvas.delete("confirm_overlay")

    def update_rematch_panel(self, status_text: str, show_accept: bool):
        if not self.canvas:
            return
        self.canvas.delete("rematch_extra")
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        status_y = board_px // 2 + 100
        self.canvas.create_text(board_px // 2, status_y,
                                text=status_text, font=("", 9), fill="#CCC",
                                tags=("overlay", "rematch_extra"))
        if show_accept:
            btn_y = status_y + 25
            self.canvas.create_rectangle(
                board_px // 2 + 10, btn_y - 13, board_px // 2 + 90, btn_y + 13,
                fill="#4CAF50", outline="",
                tags=("overlay", "rematch_extra", "accept_btn"))
            self.canvas.create_text(board_px // 2 + 50, btn_y,
                                    text="接受", font=("", 11, "bold"),
                                    fill="white",
                                    tags=("overlay", "rematch_extra", "accept_btn"))
            self.canvas.tag_bind("accept_btn", "<Button-1>",
                                 lambda e: self.cb['on_rematch_yes']())

    # ═══════════════════════════════════════════════════
    #  庆祝粒子动画
    # ═══════════════════════════════════════════════════
    def _start_celebration(self):
        if not self.canvas:
            return
        import random
        self._celebration_dots = []
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        for _ in range(30):
            x = random.randint(30, board_px - 30)
            y = random.randint(30, board_px - 30)
            r = random.randint(3, 7)
            color = random.choice(["#FFD700", "#FF6B6B", "#4FC3F7", "#81C784",
                                    "#FFB74D", "#BA68C8", "#FFF176"])
            dot_id = self.canvas.create_oval(
                x - r, y - r, x + r, y + r,
                fill=color, outline="", tags=("overlay", "confetti"))
            self._celebration_dots.append({
                "id": dot_id, "x": x, "y": y, "r": r,
                "dx": random.uniform(-3, 3),
                "dy": random.uniform(-5, -1),
                "life": random.randint(20, 50),
            })
        self._animate_celebration()

    def _animate_celebration(self):
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        to_remove = []
        for dot in self._celebration_dots:
            dot["life"] -= 1
            dot["x"] += dot["dx"]
            dot["y"] += dot["dy"]
            dot["dy"] += 0.2
            r = dot["r"]
            if dot["life"] <= 0:
                to_remove.append(dot)
                self.canvas.delete(dot["id"])
            elif 0 < dot["x"] < board_px and 0 < dot["y"] < board_px:
                self.canvas.coords(dot["id"],
                                   dot["x"] - r, dot["y"] - r,
                                   dot["x"] + r, dot["y"] + r)
        for d in to_remove:
            self._celebration_dots.remove(d)
        if self._celebration_dots:
            self._celebration_job = self.root.after(40, self._animate_celebration)

    def _stop_celebration(self):
        if self._celebration_job:
            self.root.after_cancel(self._celebration_job)
            self._celebration_job = None
        if self.canvas:
            self.canvas.delete("confetti")

    # ═══════════════════════════════════════════════════
    #  倒计时
    # ═══════════════════════════════════════════════════
    def start_countdown(self):
        self._countdown_value = TURN_TIME_LIMIT
        self.countdown_running = True
        if self.timer_label:
            self.timer_label.config(text=f"⏱ {self._countdown_value}s")
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
        self._tick_countdown()

    def reset_countdown(self):
        self._countdown_value = TURN_TIME_LIMIT
        if self.timer_label:
            self.timer_label.config(text=f"⏱ {self._countdown_value}s")

    def stop_countdown(self):
        self.countdown_running = False
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
            self._countdown_job = None

    def _tick_countdown(self):
        if not self.countdown_running:
            if self.timer_label:
                self.timer_label.config(text="")
            return
        if self.timer_label:
            self.timer_label.config(text=f"⏱ {self._countdown_value}s")
        if self._countdown_value <= 0:
            self.countdown_running = False
            return
        self._countdown_value -= 1
        self._countdown_job = self.root.after(1000, self._tick_countdown)

    # ═══════════════════════════════════════════════════
    #  通知横幅
    # ═══════════════════════════════════════════════════
    def notify(self, text: str, level: str = "info"):
        """level: info / warn / error"""
        colors = {"info": ("#333", "white"), "warn": ("#E65100", "white"),
                  "error": ("#C62828", "white")}
        bg, fg = colors.get(level, colors["info"])
        try:
            if self._note_label:
                self._note_label.config(text=text, bg=bg, fg=fg)
                self._note_label.pack(side="top", fill="x", padx=5, pady=(5, 0))
            if self._note_job:
                self.root.after_cancel(self._note_job)
            self._note_job = self.root.after(3000, self._hide_notification)
        except Exception:
            pass

    def _hide_notification(self):
        try:
            if self._note_label:
                self._note_label.pack_forget()
        except Exception:
            pass

    # ═══════════════════════════════════════════════════
    #  状态更新
    # ═══════════════════════════════════════════════════
    def update_status(self, text: str):
        if self.status_label:
            try:
                self.status_label.config(text=text)
            except Exception:
                pass

    def update_players_display(self, text: str):
        if self.players_label:
            try:
                self.players_label.config(text=text)
            except Exception:
                pass

    def update_move_log(self, moves: list[tuple[int, int, int]]):
        if not self.move_listbox:
            return
        self.move_listbox.delete(0, tk.END)
        color_names = {BLACK: "●黑", WHITE: "○白"}
        for i, (color, r, c) in enumerate(moves, 1):
            self.move_listbox.insert(tk.END,
                                     f"{i:02d}. {color_names.get(color,'?')} ({r},{c})")
        self.move_listbox.see(tk.END)

    def show_terminated(self, reason: str):
        """显示断线状态：重连按钮 + 错误通知。"""
        if self.reconnect_btn:
            self.reconnect_btn.pack(side="right", padx=10)
        self.notify(reason, "error")

    # ═══════════════════════════════════════════════════
    #  棋盘尺寸调整
    # ═══════════════════════════════════════════════════
    def resize_board(self, cell_size: int, piece_radius: int,
                     board: list[list[int]]):
        global CELL_SIZE, PIECE_RADIUS
        CELL_SIZE = cell_size
        PIECE_RADIUS = piece_radius
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        if self.canvas:
            self.canvas.config(width=board_px, height=board_px)
        self.root.geometry(f"{board_px + 220}x{board_px + 60}")
        self.draw_board(board)

    # ═══════════════════════════════════════════════════
    #  生命周期
    # ═══════════════════════════════════════════════════
    def set_on_close(self, handler):
        self.root.protocol("WM_DELETE_WINDOW", handler)

    def run(self):
        self.root.mainloop()

    def destroy(self):
        try:
            self.root.destroy()
        except Exception:
            pass
