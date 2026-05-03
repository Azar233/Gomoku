"""
五子棋联机游戏 — 自定义应用层协议封装模块。
报文格式：
  - 头部 (固定 7 字节):
      [魔数 2B] [指令类型 1B] [数据长度 4B 大端序]
  - 数据段 (可变长度): UTF-8 编码字符串
"""

import struct
from typing import Tuple, Optional

# ── 常量定义 ──────────────────────────────────────────────
MAGIC = b'\x58\x51'          # 魔数 "XQ"
HEADER_SIZE = 7               # 头部固定长度
MAX_PAYLOAD_SIZE = 64 * 1024  # 单条消息最大载荷（64KB）

# 指令类型
CMD_CONNECT = 0x01            # 客户端连接 (数据: 昵称)
CMD_PLACE   = 0x02            # 落子指令   (数据: "行,列")
CMD_UNDO    = 0x03            # 悔棋请求   (数据: 空)
CMD_RESIGN  = 0x04            # 认输请求   (数据: 空)
CMD_BROADCAST = 0x05          # 服务器广播 (数据: 棋盘状态等)
CMD_HEARTBEAT = 0x06          # 心跳指令   (数据: 空)
CMD_ERROR   = 0x07            # 错误提示   (数据: 错误消息)
CMD_GAME_START = 0x08         # 游戏开始通知
CMD_UNDO_RESULT = 0x09        # 悔棋结果 (数据: 更新后的棋盘状态)
CMD_REMATCH = 0x0A            # 请求再来一局 (数据: "yes" 或 "no")
CMD_REMATCH_ACK = 0x0B        # 再来一局状态通知 (数据: "waiting|1" 对方已准备 / "start" 开始新局 / "reject" 对方拒绝)
CMD_TIME_LIMIT = 0x0C         # 回合限时设置 (数据: "秒数|操作者昵称")

CMD_NAMES = {
    0x01: "CONNECT", 0x02: "PLACE", 0x03: "UNDO",
    0x04: "RESIGN", 0x05: "BROADCAST", 0x06: "HEARTBEAT",
    0x07: "ERROR", 0x08: "GAME_START", 0x09: "UNDO_RESULT",
    0x0A: "REMATCH", 0x0B: "REMATCH_ACK", 0x0C: "TIME_LIMIT",
}


# ── 打包函数 ──────────────────────────────────────────────
def pack_message(cmd: int, data: str = "") -> bytes:
    """将指令和数据打包为完整报文（含 7 字节头部）。"""
    data_bytes = data.encode("utf-8")
    header = struct.pack(">2s B I", MAGIC, cmd, len(data_bytes))
    return header + data_bytes


# ── 解析函数 ──────────────────────────────────────────────
def parse_header(header: bytes) -> Tuple[int, int]:
    """解析头部 7 字节，返回 (指令类型, 数据长度)。校验魔数失败则抛出 ValueError。"""
    magic, cmd, length = struct.unpack(">2s B I", header)
    if magic != MAGIC:
        raise ValueError(f"Invalid magic number: {magic!r}")
    if length > MAX_PAYLOAD_SIZE:
        raise ValueError(f"Payload too large: {length}")
    return cmd, length


def parse_message(data: bytes) -> Tuple[int, str]:
    """解析完整报文，返回 (指令类型, 数据字符串)。"""
    if len(data) < HEADER_SIZE:
        raise ValueError(f"Message too short: {len(data)} bytes")
    cmd, length = parse_header(data[:HEADER_SIZE])
    payload = data[HEADER_SIZE:HEADER_SIZE + length].decode("utf-8")
    return cmd, payload


# ── 粘包安全接收 ──────────────────────────────────────────
def recv_exact(sock, n: int) -> Optional[bytes]:
    """从 socket 精确接收 n 字节数据。返回 None 表示连接已关闭。"""
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (ConnectionResetError, ConnectionAbortedError, OSError):
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def recv_message(sock) -> Optional[Tuple[int, str]]:
    """
    从 socket 精确接收一个完整报文（先 7 字节头部，再按长度读数据段）。
    返回 (指令类型, 数据字符串) 或 None（连接关闭/错误）。
    """
    header = recv_exact(sock, HEADER_SIZE)
    if header is None:
        return None
    try:
        cmd, length = parse_header(header)
    except ValueError:
        return None  # 魔数非法，丢弃
    payload_bytes = b""
    if length > 0:
        payload_bytes = recv_exact(sock, length)
        if payload_bytes is None:
            return None
    try:
        payload = payload_bytes.decode("utf-8") if payload_bytes else ""
    except UnicodeDecodeError:
        payload = ""
    return cmd, payload
