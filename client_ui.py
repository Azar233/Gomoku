"""
五子棋联机 — GUI 渲染模块。所有 tkinter 操作集中在此，不含游戏逻辑与网络通信。
设计语言：现代 token 驱动 UI（参考 Fluent/Ant 主流体系）：清晰层级、语义色、可感知状态。
"""

import tkinter as tk
import platform

# ── UI 可调常量 ──────────────────────────────────────
CELL_SIZE = 40
PADDING = 30
PIECE_RADIUS = 17
TURN_TIME_LIMIT = 30
BOARD_SIZE = 15
BLACK = 1
WHITE = 2

# ── 设计令牌 (Design Tokens) ─────────────────────────
# 空间
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 20
SPACE_6 = 24

# 字体
FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Cascadia Mono"
FONT_HERO = (FONT_FAMILY, 30, "bold")
FONT_H2 = (FONT_FAMILY, 16, "bold")
FONT_H3 = (FONT_FAMILY, 12, "bold")
FONT_BODY = (FONT_FAMILY, 10)
FONT_CAPTION = (FONT_FAMILY, 9)
FONT_BUTTON = (FONT_FAMILY, 10, "bold")
FONT_TIMER = (FONT_FAMILY, 12, "bold")
FONT_COORD = (FONT_MONO, 8)

# 背景与层级
SURFACE_BG = "#F4F7FB"
SURFACE_SOFT = "#EAF0F7"
CARD_BG = "#FFFFFF"
CARD_BORDER = "#D3DFEC"
CARD_BORDER_STRONG = "#B8C8DA"

# 品牌与语义色
PRIMARY = "#0A84FF"
PRIMARY_HOVER = "#006EDC"
PRIMARY_ACTIVE = "#0058B5"
SUCCESS = "#12A360"
SUCCESS_HOVER = "#0E8A51"
DANGER = "#D14848"
DANGER_HOVER = "#B73D3D"
WARNING = "#D68A00"
WARNING_HOVER = "#B87400"

# 文本
TEXT_PRIMARY = "#12253D"
TEXT_SECONDARY = "#4F647F"
TEXT_MUTED = "#7187A4"
TEXT_ON_COLOR = "#FFFFFF"

# 分段按钮
SEG_BG = "#DCE7F3"
SEG_HOVER = "#C8D9EC"
SEG_ACTIVE_BG = PRIMARY
SEG_ACTIVE_FG = TEXT_ON_COLOR

# 棋盘
BOARD_BG = "#E7CFA8"
BOARD_LINE = "#9D8968"
STAR_DOT = "#7B6444"
BOARD_FRAME = "#C9B289"
BLACK_STONE = "#191B20"
BLACK_RING = "#2C3440"
WHITE_STONE = "#FAFBFF"
WHITE_RING = "#BCC8D6"

# 遮罩
SCRIM = "#0F172A"
OVERLAY_BG = "#1A2738"
OVERLAY_BORDER = "#385067"
OVERLAY_TEXT = "#EAF2FB"
OVERLAY_GOLD = "#F7C74C"

# ═══════════════════════════════════════════════════════
class GameUI:
    """游戏界面管理器，负责所有 tkinter 组件与渲染。"""

    def __init__(self, root: tk.Tk, callbacks: dict):
        """
        callbacks 字典必须包含：
          on_connect(ip, port, nick)   — 连接按钮点击
          on_click(row, col)           — 棋盘点击
          on_undo()                    — 悔棋
          on_undo_accept()             — 同意对手悔棋
          on_undo_reject()             — 拒绝对手悔棋
          on_resign()                  — 认输确认
          on_rematch_yes()             — 再来一局
          on_rematch_no()              — 离开
          on_reconnect()               — 手动重连
          on_time_change(seconds)      — 限时变更
          on_size_change(cell, radius) — 棋盘尺寸变更
          on_toggle_animation(enabled) — 动画开关
          on_toggle_sound(enabled)     — 音效开关
        """
        self.root = root
        self.cb = callbacks
        self.root.configure(bg=SURFACE_BG)

        # 控件引用（在 show_xxx_screen 中赋值）
        self._connect_status: tk.Label | None = None
        self.ip_entry: tk.Entry | None = None
        self.port_entry: tk.Entry | None = None
        self.nick_entry: tk.Entry | None = None

        self.players_label: tk.Label | None = None
        self.status_label: tk.Label | None = None
        self.timer_label: tk.Label | None = None
        self.reconnect_btn: tk.Button | None = None
        self.canvas: tk.Canvas | None = None
        self.move_listbox: tk.Listbox | None = None
        self._net_label: tk.Label | None = None

        # 倒计时
        self._countdown_value = TURN_TIME_LIMIT
        self._countdown_job = None
        self.countdown_running = False

        # 通知
        self._note_job = None

        # 庆祝动画
        self._celebration_job = None
        self._celebration_dots: list[dict] = []
        self._size_btn_refs: dict[int, tk.Button] = {}
        self._time_btn_refs: dict[int, tk.Button] = {}
        self._active_cell_size = CELL_SIZE
        self._active_time_limit = TURN_TIME_LIMIT
        self._last_move: tuple[int, int] | None = None
        self._undo_btn: tk.Button | None = None
        self._resign_btn: tk.Button | None = None
        self._hover_cell: tuple[int, int] | None = None
        self._last_animated_move: tuple[int, int] | None = None
        self._last_board_snapshot = [[0] * BOARD_SIZE for _ in range(BOARD_SIZE)]
        self._undo_overlay_visible = False
        self.enable_animation = True
        self.enable_sound = False
        self._anim_var: tk.BooleanVar | None = None
        self._sound_var: tk.BooleanVar | None = None

    # ═══════════════════════════════════════════════════
    #  辅助：创建圆角效果的 Pill 按钮 (Canvas)
    # ═══════════════════════════════════════════════════
    def _pill_button(self, canvas, x1, y1, x2, y2, text, fill, hover_fill,
                     on_click, tags=()):
        """在 Canvas 上绘制一个圆角矩形按钮，返回其 tag。"""
        tag = f"pill_{id(on_click)}"
        r = 6  # 圆角半径
        # 用多个形状模拟圆角矩形（tkinter 不支持 border-radius）
        items = []
        items.append(canvas.create_rectangle(
            x1 + r, y1, x2 - r, y2, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_rectangle(
            x1, y1 + r, x2, y2 - r, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_oval(
            x1, y1, x1 + 2*r, y1 + 2*r, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_oval(
            x2 - 2*r, y1, x2, y1 + 2*r, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_oval(
            x1, y2 - 2*r, x1 + 2*r, y2, fill=fill, outline="", tags=(tag, *tags)))
        items.append(canvas.create_oval(
            x2 - 2*r, y2 - 2*r, x2, y2, fill=fill, outline="", tags=(tag, *tags)))
        canvas.create_text((x1 + x2) // 2, (y1 + y2) // 2,
                           text=text, fill=TEXT_ON_COLOR,
                           font=("", 11, "bold"), tags=(tag, *tags))

        def on_enter(e):
            for i in items:
                canvas.itemconfig(i, fill=hover_fill)
        def on_leave(e):
            for i in items:
                canvas.itemconfig(i, fill=fill)

        canvas.tag_bind(tag, "<Button-1>", lambda e: on_click())
        canvas.tag_bind(tag, "<Enter>", on_enter)
        canvas.tag_bind(tag, "<Leave>", on_leave)
        return tag

    # ═══════════════════════════════════════════════════
    #  连接界面
    # ═══════════════════════════════════════════════════
    def _clear_root(self):
        for w in self.root.winfo_children():
            w.destroy()
        self.canvas = None

    def show_connect_screen(self, prefs: dict | None = None, local_ip: str = ""):
        self._clear_root()
        self.root.geometry("500x520")
        self.root.configure(bg=SURFACE_BG)
        prefs = prefs or {}

        # 居中容器
        container = tk.Frame(self.root, bg=SURFACE_BG)
        container.place(relx=0.5, rely=0.5, anchor="center")

        # 主卡片
        card = tk.Frame(container, bg=CARD_BG,
                        highlightbackground=CARD_BORDER_STRONG, highlightthickness=1,
                        padx=44, pady=36)
        card.pack()

        tk.Label(card, text="GOMOKU", font=FONT_CAPTION,
                 fg=PRIMARY, bg=CARD_BG).pack()
        tk.Label(card, text="五子棋联机对战", font=FONT_HERO,
                 fg=TEXT_PRIMARY, bg=CARD_BG).pack()
        tk.Label(card, text="输入服务器信息后开始匹配", font=FONT_BODY,
                 fg=TEXT_SECONDARY, bg=CARD_BG).pack(pady=(SPACE_1, SPACE_6))
        if local_ip:
            tk.Label(card, text=f"局域网提示：本机 IP 为 {local_ip}",
                     font=FONT_CAPTION, fg=TEXT_MUTED, bg=CARD_BG).pack(pady=(0, SPACE_4))

        # 字段样式
        field_font = (FONT_FAMILY, 11)
        label_font = FONT_CAPTION

        for i, (label, _, default, width) in enumerate([
            ("IP 地址", "ip", prefs.get("ip", ""), 22),
            ("端口", "port", str(prefs.get("port", 9527)), 22),
            ("昵称", "nick", prefs.get("nickname", ""), 22),
        ]):
            tk.Label(card, text=label, font=label_font,
                     fg=TEXT_SECONDARY, bg=CARD_BG, anchor="w").pack(
                fill="x", pady=(SPACE_3 if i > 0 else 0, SPACE_1))

            entry = tk.Entry(card, font=field_font,
                             bg=SURFACE_SOFT,
                             fg=TEXT_PRIMARY,
                             insertbackground=TEXT_PRIMARY,
                             relief="solid",
                             highlightbackground=CARD_BORDER,
                              highlightthickness=1,
                              width=width)
            entry.insert(0, default)
            entry.pack(ipady=8)

            if label == "IP 地址":
                self.ip_entry = entry
            elif label == "端口":
                self.port_entry = entry
            else:
                self.nick_entry = entry

        # 连接按钮
        btn_frame = tk.Frame(card, bg=CARD_BG)
        btn_frame.pack(pady=(SPACE_6, SPACE_2))
        btn = tk.Button(btn_frame, text="连接服务器",
                        command=self._on_connect_btn,
                        font=(FONT_FAMILY, 12, "bold"),
                        bg=PRIMARY, fg=TEXT_ON_COLOR,
                        activebackground=PRIMARY_HOVER,
                        activeforeground=TEXT_ON_COLOR,
                        relief="flat", padx=34, pady=10,
                        cursor="hand2", borderwidth=0)
        btn.pack()

        self._connect_status = tk.Label(card, text="", font=FONT_CAPTION,
                                        fg=TEXT_SECONDARY, bg=CARD_BG)
        self._connect_status.pack(pady=(SPACE_2, 0))

        # 偏好开关
        self.enable_animation = bool(prefs.get("enable_animation", True))
        self.enable_sound = bool(prefs.get("enable_sound", False))
        self._anim_var = tk.BooleanVar(value=self.enable_animation)
        self._sound_var = tk.BooleanVar(value=self.enable_sound)
        pref_frame = tk.Frame(card, bg=CARD_BG)
        pref_frame.pack(pady=(SPACE_4, 0))
        tk.Checkbutton(
            pref_frame, text="落子动画", variable=self._anim_var,
            command=lambda: self.cb['on_toggle_animation'](bool(self._anim_var.get())),
            bg=CARD_BG, fg=TEXT_SECONDARY, activebackground=CARD_BG, selectcolor=CARD_BG,
            font=FONT_CAPTION
        ).pack(side="left", padx=SPACE_2)
        tk.Checkbutton(
            pref_frame, text="音效", variable=self._sound_var,
            command=lambda: self.cb['on_toggle_sound'](bool(self._sound_var.get())),
            bg=CARD_BG, fg=TEXT_SECONDARY, activebackground=CARD_BG, selectcolor=CARD_BG,
            font=FONT_CAPTION
        ).pack(side="left", padx=SPACE_2)

        self.root.bind("<Return>", lambda e: self._on_connect_btn())

    def set_connect_status(self, text: str, color: str):
        if self._connect_status:
            mapped = color
            if color == "red":
                mapped = DANGER
            elif color == "green":
                mapped = SUCCESS
            elif color == "orange":
                mapped = WARNING
            elif color == "blue":
                mapped = PRIMARY
            self._connect_status.config(text=text, fg=mapped)

    def _on_connect_btn(self):
        ip = self.ip_entry.get().strip() if self.ip_entry else ""
        try:
            port = int(self.port_entry.get().strip()) if self.port_entry else 0
        except ValueError:
            self.set_connect_status("端口必须为整数", DANGER)
            return
        nickname = self.nick_entry.get().strip() if self.nick_entry else ""
        if not nickname:
            self.set_connect_status("请输入昵称", DANGER)
            return
        self.set_connect_status("正在连接...", PRIMARY)
        self.root.update()
        self.cb['on_connect'](ip, port, nickname)

    def _handle_size_click(self, cell_size: int, piece_radius: int):
        self._active_cell_size = cell_size
        self._update_size_buttons()
        self.cb['on_size_change'](cell_size, piece_radius)

    def _handle_time_click(self, seconds: int):
        self._active_time_limit = seconds
        self._update_time_buttons()
        self.cb['on_time_change'](seconds)

    def _update_size_buttons(self):
        for cell, btn in self._size_btn_refs.items():
            is_active = (cell == self._active_cell_size)
            btn.config(
                bg=SEG_ACTIVE_BG if is_active else SEG_BG,
                fg=SEG_ACTIVE_FG if is_active else TEXT_PRIMARY
            )

    def _update_time_buttons(self):
        for secs, btn in self._time_btn_refs.items():
            is_active = (secs == self._active_time_limit)
            btn.config(
                bg=SEG_ACTIVE_BG if is_active else SEG_BG,
                fg=SEG_ACTIVE_FG if is_active else TEXT_PRIMARY
            )

    # ═══════════════════════════════════════════════════
    #  游戏界面
    # ═══════════════════════════════════════════════════
    def show_game_screen(self):
        self._clear_root()
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        panel_w = 300
        self.root.geometry(f"{board_px + panel_w + 40}x{board_px + 90}")
        self.root.configure(bg=SURFACE_BG)

        # ── 顶部信息栏 ──
        info_frame = tk.Frame(self.root, bg=CARD_BG,
                              highlightbackground=CARD_BORDER,
                              highlightthickness=1)
        info_frame.pack(side="top", fill="x", padx=SPACE_4, pady=(SPACE_4, SPACE_2), ipady=SPACE_2)

        self.players_label = tk.Label(info_frame, text="",
                                      font=FONT_H3,
                                      fg=TEXT_PRIMARY, bg=CARD_BG)
        self.players_label.pack(side="left", padx=SPACE_4)

        self.status_label = tk.Label(info_frame, text="等待游戏开始...",
                                     font=FONT_BODY,
                                     fg=TEXT_SECONDARY, bg=CARD_BG)
        self.status_label.pack(side="left", padx=SPACE_2)

        self.timer_label = tk.Label(info_frame, text="",
                                    font=FONT_TIMER,
                                    fg=DANGER, bg=CARD_BG)
        self.timer_label.pack(side="right", padx=SPACE_4)
        self._net_label = tk.Label(info_frame, text="网络: --",
                                   font=FONT_CAPTION,
                                   fg=TEXT_MUTED, bg=CARD_BG)
        self._net_label.pack(side="right", padx=(0, SPACE_3))

        self.reconnect_btn = tk.Button(
            info_frame, text="重连", command=self.cb['on_reconnect'],
            font=FONT_CAPTION, bg=PRIMARY, fg=TEXT_ON_COLOR,
            activebackground=PRIMARY_HOVER, relief="flat",
            cursor="hand2", padx=SPACE_2, borderwidth=0)

        # ── 主区域 ──
        main_frame = tk.Frame(self.root, bg=SURFACE_BG)
        main_frame.pack(side="top", padx=SPACE_4, pady=(0, SPACE_4))

        self._note_job = None

        self.canvas = tk.Canvas(main_frame, width=board_px, height=board_px,
                                bg=BOARD_BG, cursor="hand2",
                                highlightthickness=1,
                                highlightbackground=BOARD_FRAME)
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Motion>", self._on_canvas_motion)
        self.canvas.bind("<Leave>", self._on_canvas_leave)

        # ── 右侧面板 ──
        panel = tk.Frame(main_frame, width=panel_w, bg=CARD_BG,
                         highlightbackground=CARD_BORDER_STRONG,
                         highlightthickness=1)
        panel.pack(side="right", fill="y", padx=(SPACE_3, 0))
        panel.pack_propagate(False)

        # 操作区
        tk.Label(panel, text="操作", font=FONT_H2,
                 fg=TEXT_PRIMARY, bg=CARD_BG).pack(pady=(SPACE_4, SPACE_3))

        btn_opts = {"font": FONT_BUTTON, "relief": "flat",
                    "activeforeground": TEXT_ON_COLOR, "cursor": "hand2",
                    "borderwidth": 0, "padx": SPACE_4, "pady": SPACE_2, "width": 16}

        self._undo_btn = tk.Button(panel, text="悔棋 (Ctrl+R)", command=self.cb['on_undo'],
                                   bg=WARNING, fg=TEXT_ON_COLOR,
                                   activebackground=WARNING_HOVER, **btn_opts)
        self._undo_btn.pack(pady=SPACE_1)

        self._resign_btn = tk.Button(panel, text="认输 (Ctrl+G)", command=self.cb['on_resign'],
                                     bg=DANGER, fg=TEXT_ON_COLOR,
                                     activebackground=DANGER_HOVER, **btn_opts)
        self._resign_btn.pack(pady=SPACE_1)

        # 分隔
        tk.Frame(panel, height=1, bg=CARD_BORDER).pack(fill="x", padx=SPACE_5, pady=SPACE_4)

        # 尺寸
        tk.Label(panel, text="棋盘/棋子大小", font=FONT_CAPTION,
                 fg=TEXT_SECONDARY, bg=CARD_BG).pack()
        size_frame = tk.Frame(panel, bg=CARD_BG)
        size_frame.pack(pady=SPACE_1)
        size_btn = {"font": FONT_CAPTION, "relief": "flat", "borderwidth": 0,
                    "cursor": "hand2", "padx": SPACE_2, "pady": SPACE_1, "width": 5}
        self._size_btn_refs = {}
        for label, cell, radius in [("小", 30, 13), ("中", 40, 17), ("大", 50, 22)]:
            b = tk.Button(size_frame, text=label,
                          command=lambda c=cell, r=radius: self._handle_size_click(c, r),
                          bg=SEG_ACTIVE_BG if cell == self._active_cell_size else SEG_BG,
                          fg=SEG_ACTIVE_FG if cell == self._active_cell_size else TEXT_PRIMARY,
                          activebackground=SEG_HOVER, **size_btn)
            b.pack(side="left", padx=SPACE_1)
            self._size_btn_refs[cell] = b

        # 限时
        tk.Label(panel, text="回合限时", font=FONT_CAPTION,
                 fg=TEXT_SECONDARY, bg=CARD_BG).pack(pady=(SPACE_4, 0))
        tk.Label(panel, text="由服务器统一生效", font=FONT_CAPTION,
                 fg=TEXT_MUTED, bg=CARD_BG).pack()
        time_frame = tk.Frame(panel, bg=CARD_BG)
        time_frame.pack(pady=SPACE_1)
        self._time_btn_refs = {}
        for secs in [30, 60, 90]:
            b = tk.Button(time_frame, text=f"{secs}s",
                          command=lambda s=secs: self._handle_time_click(s),
                          bg=SEG_ACTIVE_BG if secs == self._active_time_limit else SEG_BG,
                          fg=SEG_ACTIVE_FG if secs == self._active_time_limit else TEXT_PRIMARY,
                          activebackground=SEG_HOVER, **size_btn)
            b.pack(side="left", padx=SPACE_1)
            self._time_btn_refs[secs] = b

        # 分隔
        tk.Frame(panel, height=1, bg=CARD_BORDER).pack(fill="x", padx=SPACE_5, pady=SPACE_4)

        # 落子记录
        tk.Label(panel, text="落子记录", font=FONT_CAPTION,
                 fg=TEXT_SECONDARY, bg=CARD_BG).pack()
        list_frame = tk.Frame(panel, bg=CARD_BORDER)
        list_frame.pack(pady=SPACE_1, padx=SPACE_4, fill="both", expand=True)
        self.move_listbox = tk.Listbox(list_frame, height=12, width=24,
                                       font=(FONT_MONO, 9),
                                       bg=SURFACE_SOFT, fg=TEXT_PRIMARY,
                                       selectbackground=PRIMARY,
                                       selectforeground=TEXT_ON_COLOR,
                                       relief="flat",
                                       highlightthickness=0,
                                       borderwidth=1)
        self.move_listbox.pack(fill="both", expand=True)

        # 分隔
        tk.Frame(panel, height=1, bg=CARD_BORDER).pack(fill="x", padx=SPACE_5, pady=SPACE_4)

        # 开关
        tk.Label(panel, text="体验", font=FONT_CAPTION,
                 fg=TEXT_SECONDARY, bg=CARD_BG).pack()
        toggle_frame = tk.Frame(panel, bg=CARD_BG)
        toggle_frame.pack(pady=SPACE_1)
        self._anim_var = tk.BooleanVar(value=self.enable_animation)
        self._sound_var = tk.BooleanVar(value=self.enable_sound)
        tk.Checkbutton(
            toggle_frame, text="动画", variable=self._anim_var,
            command=lambda: self.cb['on_toggle_animation'](bool(self._anim_var.get())),
            bg=CARD_BG, fg=TEXT_SECONDARY, activebackground=CARD_BG, selectcolor=CARD_BG,
            font=FONT_CAPTION
        ).pack(side="left", padx=SPACE_2)
        tk.Checkbutton(
            toggle_frame, text="音效", variable=self._sound_var,
            command=lambda: self.cb['on_toggle_sound'](bool(self._sound_var.get())),
            bg=CARD_BG, fg=TEXT_SECONDARY, activebackground=CARD_BG, selectcolor=CARD_BG,
            font=FONT_CAPTION
        ).pack(side="left", padx=SPACE_2)

        # 键盘绑定
        self.root.bind("<Control-r>", lambda e: self.cb['on_undo']())
        self.root.bind("<Control-R>", lambda e: self.cb['on_undo']())
        self.root.bind("<Control-g>", lambda e: self.cb['on_resign']())
        self.root.bind("<Control-G>", lambda e: self.cb['on_resign']())
        self.set_action_buttons_enabled(True)

    # ═══════════════════════════════════════════════════
    #  棋盘绘制
    # ═══════════════════════════════════════════════════
    def draw_board(self, board: list[list[int]]):
        if not self.canvas:
            return
        self._last_board_snapshot = [row[:] for row in board]
        self.canvas.delete("board")
        board_width = (BOARD_SIZE - 1) * CELL_SIZE

        # 坐标轴（上方字母、左侧数字）
        for c in range(BOARD_SIZE):
            cx = PADDING + c * CELL_SIZE
            self.canvas.create_text(
                cx, PADDING - max(10, int(CELL_SIZE * 0.45)),
                text=chr(ord("A") + c), fill=TEXT_MUTED, font=FONT_COORD, tags="board"
            )
        for r in range(BOARD_SIZE):
            cy = PADDING + r * CELL_SIZE
            self.canvas.create_text(
                PADDING - max(10, int(CELL_SIZE * 0.45)), cy,
                text=str(r + 1), fill=TEXT_MUTED, font=FONT_COORD, tags="board"
            )

        # 网格线
        for i in range(BOARD_SIZE):
            coord = PADDING + i * CELL_SIZE
            self.canvas.create_line(PADDING, coord, PADDING + board_width, coord,
                                    fill=BOARD_LINE, width=1, tags="board")
            self.canvas.create_line(coord, PADDING, coord, PADDING + board_width,
                                    fill=BOARD_LINE, width=1, tags="board")

        # 星位点
        star_points = [(3, 3), (3, 7), (3, 11), (7, 3), (7, 7), (7, 11),
                       (11, 3), (11, 7), (11, 11)]
        for r, c in star_points:
            cx = PADDING + c * CELL_SIZE
            cy = PADDING + r * CELL_SIZE
            self.canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3,
                                    fill=STAR_DOT, outline="", tags="board")

        # 棋子
        for r in range(BOARD_SIZE):
            for c in range(BOARD_SIZE):
                if board[r][c] != 0:
                    self._draw_piece(r, c, board[r][c])

        # 悬停十字准星
        self._draw_hover_crosshair()

        # 最后一步高亮
        if self._last_move:
            r, c = self._last_move
            cx = PADDING + c * CELL_SIZE
            cy = PADDING + r * CELL_SIZE
            mark_r = max(4, PIECE_RADIUS * 0.35)
            self.canvas.create_oval(
                cx - mark_r, cy - mark_r, cx + mark_r, cy + mark_r,
                fill="#FFD34D", outline="#B78600", width=1, tags="board"
            )
            # 新落子的短动画（只对最新一次，避免整盘重绘反复触发）
            if self._last_animated_move != self._last_move:
                self._last_animated_move = self._last_move
                color = board[r][c] if 0 <= r < BOARD_SIZE and 0 <= c < BOARD_SIZE else 0
                if color in (BLACK, WHITE):
                    if self.enable_animation:
                        self._animate_last_piece(r, c, color)
                    self._play_piece_sound("place")

        self._lift_modal_layers()

    def _draw_piece(self, row: int, col: int, color: int):
        if not self.canvas:
            return
        cx = PADDING + col * CELL_SIZE
        cy = PADDING + row * CELL_SIZE
        r = PIECE_RADIUS

        if color == BLACK:
            # 玻璃质感黑子：深色底 + 高光点
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=BLACK_STONE, outline=BLACK_RING, width=1, tags="board")
            # 高光
            hl_r = r * 0.3
            self.canvas.create_oval(
                cx - hl_r - 2, cy - hl_r - 2,
                cx + hl_r - 2, cy + hl_r - 2,
                fill="", outline="#555555", width=1, tags="board")
        else:
            # 玻璃质感白子：浅色底 + 高光点 + 浅灰环
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=WHITE_STONE, outline=WHITE_RING, width=1, tags="board")
            hl_r = r * 0.3
            self.canvas.create_oval(
                cx - hl_r - 2, cy - hl_r - 2,
                cx + hl_r - 2, cy + hl_r - 2,
                fill="", outline="#E0E0E0", width=1, tags="board")

    def _draw_piece_scaled(self, row: int, col: int, color: int, scale: float):
        if not self.canvas:
            return
        cx = PADDING + col * CELL_SIZE
        cy = PADDING + row * CELL_SIZE
        r = max(2, PIECE_RADIUS * scale)
        if color == BLACK:
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=BLACK_STONE, outline=BLACK_RING, width=1, tags="fx")
        else:
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                fill=WHITE_STONE, outline=WHITE_RING, width=1, tags="fx")

    def _play_piece_sound(self, action: str = "place"):
        if not self.enable_sound:
            return
        try:
            if platform.system().lower().startswith("win"):
                import winsound  # type: ignore
                freq = 880 if action == "place" else 660
                winsound.Beep(freq, 40)
            else:
                self.root.bell()
        except Exception:
            pass

    def play_action_sound(self, action: str = "place"):
        """供外部安全触发音效（如撤销/系统动作）。"""
        self._play_piece_sound(action)

    def _animate_last_piece(self, row: int, col: int, color: int):
        if not self.canvas:
            return
        self.canvas.delete("fx")
        frames = [0.55, 0.72, 0.88, 1.0]

        def step(idx: int):
            if not self.canvas:
                return
            if idx >= len(frames):
                self.canvas.delete("fx")
                return
            self.canvas.delete("fx")
            self._draw_piece_scaled(row, col, color, frames[idx])
            self.canvas.lift("fx")
            self.canvas.lift("board")
            self.root.after(25, lambda: step(idx + 1))

        step(0)

    def _draw_hover_crosshair(self):
        if not self.canvas or self._hover_cell is None:
            return
        row, col = self._hover_cell
        if not (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE):
            return
        cx = PADDING + col * CELL_SIZE
        cy = PADDING + row * CELL_SIZE
        board_min = PADDING
        board_max = PADDING + (BOARD_SIZE - 1) * CELL_SIZE
        self.canvas.create_line(board_min, cy, board_max, cy,
                                fill="#2A8FFF", width=1, dash=(2, 3), tags="board")
        self.canvas.create_line(cx, board_min, cx, board_max,
                                fill="#2A8FFF", width=1, dash=(2, 3), tags="board")

    # ═══════════════════════════════════════════════════
    #  棋盘点击
    # ═══════════════════════════════════════════════════
    def _on_canvas_click(self, event):
        if self._undo_overlay_visible:
            return
        overlapping = self.canvas.find_overlapping(
            event.x - 1, event.y - 1, event.x + 1, event.y + 1)
        for item in overlapping:
            tags = self.canvas.gettags(item)
            if "confirm_overlay" in tags or "overlay" in tags or "undo_overlay" in tags:
                return
        col = round((event.x - PADDING) / CELL_SIZE)
        row = round((event.y - PADDING) / CELL_SIZE)
        self.cb['on_click'](row, col)

    def _on_canvas_motion(self, event):
        if not self.canvas or self._undo_overlay_visible:
            return
        col = round((event.x - PADDING) / CELL_SIZE)
        row = round((event.y - PADDING) / CELL_SIZE)
        new_hover = (row, col) if (0 <= row < BOARD_SIZE and 0 <= col < BOARD_SIZE) else None
        if new_hover != self._hover_cell:
            self._hover_cell = new_hover
            # 只重绘棋盘层，不改业务状态
            self.draw_board(self._get_current_board_snapshot())

    def _on_canvas_leave(self, _event):
        if self._undo_overlay_visible:
            return
        if self._hover_cell is not None:
            self._hover_cell = None
            self.draw_board(self._get_current_board_snapshot())

    def _get_current_board_snapshot(self):
        return [row[:] for row in self._last_board_snapshot]

    # ═══════════════════════════════════════════════════
    #  毛玻璃遮罩辅助
    # ═══════════════════════════════════════════════════
    def _glass_overlay_bg(self, tag, alpha_steps=5):
        """模拟毛玻璃暗色遮罩：绘制多层半透明条纹。"""
        if not self.canvas:
            return
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        # 使用交叉的细线模拟磨砂质感
        for i in range(0, board_px, alpha_steps):
            shade = f"#{int(15*(1 - i/board_px)):02x}{int(23*(1 - i/board_px)):02x}{int(42*(1 - i/board_px)):02x}"
            self.canvas.create_line(i, 0, i, board_px, fill=shade, tags=tag)
        # 主 scrim 矩形
        self.canvas.create_rectangle(0, 0, board_px, board_px,
                                     fill="", outline="", tags=tag)

    # ═══════════════════════════════════════════════════
    #  确认遮罩（认输）
    # ═══════════════════════════════════════════════════
    def show_confirm_overlay(self):
        if not self.canvas:
            return
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2

        # scrim
        self.canvas.create_rectangle(0, 0, board_px, board_px,
                                     fill=SCRIM, stipple="gray25",
                                     outline="", tags="confirm_overlay")

        # frosted card
        card_w, card_h = 300, 110
        cx, cy = board_px // 2, board_px // 2
        x1, y1 = cx - card_w // 2, cy - card_h // 2
        x2, y2 = cx + card_w // 2, cy + card_h // 2

        self.canvas.create_rectangle(x1, y1, x2, y2,
                                     fill=OVERLAY_BG, outline=OVERLAY_BORDER,
                                     width=1, tags="confirm_overlay")

        self.canvas.create_text(cx, y1 + 28,
                                text="确定要认输吗？", font=("", 14, "bold"),
                                fill=OVERLAY_TEXT, tags="confirm_overlay")

        # 确认按钮
        btn_w = 96
        self._pill_button(self.canvas,
                          cx - btn_w - 12, y1 + 52, cx - 12, y1 + 80,
                          "确认认输", DANGER, DANGER_HOVER,
                          self._on_confirm_yes, ("confirm_overlay",))
        # 取消按钮
        self._pill_button(self.canvas,
                          cx + 12, y1 + 52, cx + btn_w + 12, y1 + 80,
                          "取消", "#64748B", "#475569",
                          self.hide_confirm_overlay, ("confirm_overlay",))

        # 点击 scrim 也可取消
        self.canvas.tag_bind("confirm_overlay", "<Button-1>",
                             lambda e: None)  # 阻止穿透

    def _on_confirm_yes(self):
        self.hide_confirm_overlay()
        self.cb['on_resign_confirm']()

    def hide_confirm_overlay(self):
        if self.canvas:
            self.canvas.delete("confirm_overlay")

    # ═══════════════════════════════════════════════════
    #  悔棋请求遮罩（同意/拒绝）
    # ═══════════════════════════════════════════════════
    def show_undo_request_overlay(self, requester: str):
        if not self.canvas:
            return
        self.hide_undo_request_overlay()
        self._undo_overlay_visible = True
        self._hover_cell = None
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2

        self.canvas.create_rectangle(
            0, 0, board_px, board_px,
            fill=SCRIM, stipple="gray25", outline="", tags="undo_overlay"
        )

        card_w, card_h = 320, 120
        cx, cy = board_px // 2, board_px // 2
        x1, y1 = cx - card_w // 2, cy - card_h // 2
        x2, y2 = cx + card_w // 2, cy + card_h // 2
        self.canvas.create_rectangle(
            x1, y1, x2, y2,
            fill=OVERLAY_BG, outline=OVERLAY_BORDER, width=1, tags="undo_overlay"
        )

        self.canvas.create_text(
            cx, y1 + 30,
            text=f"{requester} 请求悔棋，是否同意？",
            font=("", 13, "bold"), fill=OVERLAY_TEXT, tags="undo_overlay"
        )

        btn_w = 92
        self._pill_button(
            self.canvas,
            cx - btn_w - 14, y1 + 62, cx - 14, y1 + 90,
            "同意", SUCCESS, SUCCESS_HOVER,
            self._on_undo_accept, ("undo_overlay",)
        )
        self._pill_button(
            self.canvas,
            cx + 14, y1 + 62, cx + btn_w + 14, y1 + 90,
            "拒绝", DANGER, DANGER_HOVER,
            self._on_undo_reject, ("undo_overlay",)
        )

        self.canvas.tag_bind("undo_overlay", "<Button-1>", lambda e: None)
        self._lift_modal_layers()

    def _on_undo_accept(self):
        self.hide_undo_request_overlay()
        self.cb['on_undo_accept']()

    def _on_undo_reject(self):
        self.hide_undo_request_overlay()
        self.cb['on_undo_reject']()

    def hide_undo_request_overlay(self):
        if self.canvas:
            self.canvas.delete("undo_overlay")
        self._undo_overlay_visible = False

    # ═══════════════════════════════════════════════════
    #  胜利结算遮罩
    # ═══════════════════════════════════════════════════
    def show_game_over_overlay(self, result: str):
        if not self.canvas:
            return
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2

        # scrim
        self.canvas.create_rectangle(0, 0, board_px, board_px,
                                     fill=SCRIM, stipple="gray25",
                                     outline="", tags="overlay")

        card_w, card_h = 320, 150
        cx, cy = board_px // 2, board_px // 2
        x1, y1 = cx - card_w // 2, cy - card_h // 2
        x2, y2 = cx + card_w // 2, cy + card_h // 2

        self.canvas.create_rectangle(x1, y1, x2, y2,
                                     fill=OVERLAY_BG, outline=OVERLAY_BORDER,
                                     width=1, tags="overlay")

        if "黑" in result:
            emoji = "⚫"
        elif "白" in result:
            emoji = "⚪"
        else:
            emoji = "🤝"
        self.canvas.create_text(cx, y1 + 32,
                                text=f"{emoji}  {result}  {emoji}",
                                font=("", 20, "bold"), fill=OVERLAY_GOLD,
                                tags="overlay")

        btn_y = y1 + 76
        btn_w = 100
        # 再来一局
        self._pill_button(self.canvas,
                          cx - btn_w - 14, btn_y - 14, cx - 14, btn_y + 14,
                          "再来一局", SUCCESS, SUCCESS_HOVER,
                          lambda: self.cb['on_rematch_yes'](), ("overlay",))
        # 离开
        self._pill_button(self.canvas,
                          cx + 14, btn_y - 14, cx + btn_w + 14, btn_y + 14,
                          "离开", "#64748B", "#475569",
                          lambda: self.cb['on_rematch_no'](), ("overlay",))

        # 状态文字区域占位
        self.canvas.create_text(cx, y2 + 20, text="",
                                font=("", 9), fill=OVERLAY_TEXT,
                                tags=("overlay", "rematch_status_text"))

        self._start_celebration()

    def hide_game_over_overlay(self):
        self._stop_celebration()
        if self.canvas:
            self.canvas.delete("overlay")
            self.canvas.delete("confirm_overlay")
            self.canvas.delete("undo_overlay")
        self._undo_overlay_visible = False

    def _lift_modal_layers(self):
        """确保模态层始终位于棋盘层之上。"""
        if not self.canvas:
            return
        self.canvas.lift("confirm_overlay")
        self.canvas.lift("overlay")
        self.canvas.lift("undo_overlay")

    def set_last_move(self, row: int, col: int):
        self._last_move = (row, col)

    def clear_last_move(self):
        self._last_move = None
        self._last_animated_move = None

    def set_action_buttons_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        disabled_bg = "#C6D2DE"
        disabled_fg = "#7B8A99"

        if self._undo_btn:
            self._undo_btn.config(
                state=state,
                bg=WARNING if enabled else disabled_bg,
                fg=TEXT_ON_COLOR if enabled else disabled_fg,
                activebackground=WARNING_HOVER if enabled else disabled_bg
            )
        if self._resign_btn:
            self._resign_btn.config(
                state=state,
                bg=DANGER if enabled else disabled_bg,
                fg=TEXT_ON_COLOR if enabled else disabled_fg,
                activebackground=DANGER_HOVER if enabled else disabled_bg
            )

    def update_rematch_panel(self, status_text: str, show_accept: bool = False,
                             show_reject: bool = False):
        if not self.canvas:
            return
        self.canvas.delete("rematch_extra")

        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        card_h = 150
        y2 = board_px // 2 + card_h // 2
        status_y = y2 + 34

        self.canvas.create_text(board_px // 2, status_y,
                                text=status_text, font=("", 9),
                                fill="#94A3B8", tags=("overlay", "rematch_extra"))

        if show_accept or show_reject:
            btn_y = status_y + 26
            btn_w = 90
            if show_accept and show_reject:
                self._pill_button(self.canvas,
                                  board_px // 2 - btn_w - 12, btn_y - 13,
                                  board_px // 2 - 12, btn_y + 13,
                                  "接受", SUCCESS, SUCCESS_HOVER,
                                  lambda: self.cb['on_rematch_yes'](),
                                  ("overlay", "rematch_extra"))
                self._pill_button(self.canvas,
                                  board_px // 2 + 12, btn_y - 13,
                                  board_px // 2 + btn_w + 12, btn_y + 13,
                                  "拒绝", DANGER, DANGER_HOVER,
                                  lambda: self.cb['on_rematch_reject'](),
                                  ("overlay", "rematch_extra"))
            elif show_accept:
                self._pill_button(self.canvas,
                                  board_px // 2 - btn_w // 2, btn_y - 13,
                                  board_px // 2 + btn_w // 2, btn_y + 13,
                                  "接受", SUCCESS, SUCCESS_HOVER,
                                  lambda: self.cb['on_rematch_yes'](),
                                  ("overlay", "rematch_extra"))
            elif show_reject:
                self._pill_button(self.canvas,
                                  board_px // 2 - btn_w // 2, btn_y - 13,
                                  board_px // 2 + btn_w // 2, btn_y + 13,
                                  "取消", "#64748B", "#475569",
                                  lambda: self.cb['on_rematch_reject'](),
                                  ("overlay", "rematch_extra"))

    # ═══════════════════════════════════════════════════
    #  庆祝粒子动画
    # ═══════════════════════════════════════════════════
    def _start_celebration(self):
        if not self.canvas:
            return
        import random
        self._celebration_dots = []
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        colors = ["#FBBF24", "#F472B6", "#38BDF8", "#34D399",
                  "#FB923C", "#A78BFA", "#FDE047"]
        for _ in range(30):
            x = random.randint(30, board_px - 30)
            y = random.randint(30, board_px - 30)
            r = random.randint(3, 7)
            color = random.choice(colors)
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
    def start_countdown(self, seconds: int | None = None):
        if seconds is None:
            seconds = TURN_TIME_LIMIT
        self._active_time_limit = seconds
        self._update_time_buttons()
        self._countdown_value = seconds
        self.countdown_running = True
        if self.timer_label:
            self.timer_label.config(text=f"⏱ {self._countdown_value}s")
        if self._countdown_job:
            self.root.after_cancel(self._countdown_job)
        self._tick_countdown()

    def reset_countdown(self, seconds: int | None = None):
        if seconds is None:
            seconds = TURN_TIME_LIMIT
        self._active_time_limit = seconds
        self._update_time_buttons()
        self._countdown_value = seconds
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
            if self._countdown_value <= 10:
                timer_color = DANGER
            elif self._countdown_value <= 20:
                timer_color = WARNING
            else:
                timer_color = PRIMARY
            self.timer_label.config(fg=timer_color)
            self.timer_label.config(text=f"⏱ {self._countdown_value}s")
        if self._countdown_value <= 0:
            self.countdown_running = False
            return
        self._countdown_value -= 1
        self._countdown_job = self.root.after(1000, self._tick_countdown)

    # ═══════════════════════════════════════════════════
    #  通知浮层（Canvas 居中弹出，不改变布局）
    # ═══════════════════════════════════════════════════
    def notify(self, text: str, level: str = "info"):
        """level: info / warn / error — 在棋盘中央显示浮层，3 秒后消失。"""
        if not self.canvas:
            return
        color_map = {
            "info": (TEXT_PRIMARY, "#EAF2FF"),
            "warn": ("#744A00", "#FFF3D6"),
            "error": ("#8F2E2E", "#FEE5E5"),
        }
        fg, bg = color_map.get(level, color_map["info"])

        self.canvas.delete("notify")
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        cx, cy = board_px // 2, board_px // 2

        font = ("", 12, "bold")
        text_id = self.canvas.create_text(cx, cy, text=text, font=font, fill=fg,
                                          tags="notify")
        bbox = self.canvas.bbox(text_id)
        if bbox:
            pad_x, pad_y = 28, 14
            x1, y1, x2, y2 = bbox
            # 圆角背景
            r = 8
            self.canvas.create_rectangle(
                x1 - pad_x + r, y1 - pad_y, x2 + pad_x - r, y2 + pad_y,
                fill=bg, outline="#E2E8F0", tags="notify")
            self.canvas.create_rectangle(
                x1 - pad_x, y1 - pad_y + r, x2 + pad_x, y2 + pad_y - r,
                fill=bg, outline="", tags="notify")
            for (ox, oy) in [(x1 - pad_x, y1 - pad_y), (x2 + pad_x - 2*r, y1 - pad_y),
                              (x1 - pad_x, y2 + pad_y - 2*r), (x2 + pad_x - 2*r, y2 + pad_y - 2*r)]:
                self.canvas.create_oval(ox, oy, ox + 2*r, oy + 2*r,
                                        fill=bg, outline="", tags="notify")
            self.canvas.lift(text_id)

        if self._note_job:
            self.root.after_cancel(self._note_job)
        self._note_job = self.root.after(3000, self._hide_notification)

    def _hide_notification(self):
        if self.canvas:
            self.canvas.delete("notify")

    # ═══════════════════════════════════════════════════
    #  状态更新
    # ═══════════════════════════════════════════════════
    def update_status(self, text: str):
        if self.status_label:
            try:
                self.status_label.config(text=text)
                low = text.lower()
                if "结束" in text or "断开" in text or "中断" in text:
                    self.status_label.config(fg=DANGER)
                elif "等待" in text or "重连" in text:
                    self.status_label.config(fg=WARNING)
                elif "当前回合" in text:
                    self.status_label.config(fg=PRIMARY)
                    if "黑棋" in text:
                        self.players_label.config(fg="#2A3A51")
                    elif "白棋" in text:
                        self.players_label.config(fg="#4A5F7A")
                elif "成功" in low:
                    self.status_label.config(fg=SUCCESS)
                else:
                    self.status_label.config(fg=TEXT_SECONDARY)
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
        self.update_net_quality(-1)

    def update_net_quality(self, rtt_ms: int):
        if not self._net_label:
            return
        if rtt_ms < 0:
            self._net_label.config(text="网络: 离线", fg=TEXT_MUTED)
            return
        if rtt_ms <= 80:
            quality = "优"
            color = SUCCESS
        elif rtt_ms <= 180:
            quality = "良"
            color = WARNING
        else:
            quality = "差"
            color = DANGER
        self._net_label.config(text=f"网络: {quality} ({rtt_ms}ms)", fg=color)

    # ═══════════════════════════════════════════════════
    #  棋盘尺寸调整
    # ═══════════════════════════════════════════════════
    def resize_board(self, cell_size: int, piece_radius: int,
                     board: list[list[int]]):
        global CELL_SIZE, PIECE_RADIUS, PADDING
        CELL_SIZE = cell_size
        PIECE_RADIUS = piece_radius
        PADDING = max(18, int(CELL_SIZE * 0.75))
        self._active_cell_size = cell_size
        self._update_size_buttons()
        board_px = BOARD_SIZE * CELL_SIZE + PADDING * 2
        if self.canvas:
            self.canvas.config(width=board_px, height=board_px)
        # 右侧面板与留白根据棋盘尺寸微调，保证大/小棋盘都协调
        panel_w = max(260, min(320, int(board_px * 0.38)))
        self.root.geometry(f"{board_px + panel_w + 40}x{board_px + 90}")
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
