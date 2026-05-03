"""
五子棋联机 — 网络通信模块。不依赖 tkinter，纯 socket + 回调。
"""

import socket
import threading
import time
from protocol import (
    recv_message, pack_message,
    CMD_CONNECT, CMD_HEARTBEAT,
)


class NetworkManager:
    """TCP 客户端网络管理器，通过回调通知上层。"""

    def __init__(self, on_message, on_disconnected):
        """
        on_message(cmd: int, data: str) — 收到完整报文
        on_disconnected(reason: str)  — 连接断开（网络错误/心跳超时）
        """
        self._on_message = on_message
        self._on_disconnected = on_disconnected
        self.sock: socket.socket | None = None
        self.running = False
        self._server = ("127.0.0.1", 9527)
        self._last_heartbeat_ack = 0.0
        self.reconnect_attempts = 0
        self._lock = threading.Lock()
        self._disconnect_notified = False
        self._last_heartbeat_sent = 0.0
        self._rtt_ms = -1

    # ── 连接 / 断开 ────────────────────────────────────
    def connect(self, ip: str, port: int, nickname: str) -> bool:
        """建立 TCP 连接并发送 CONNECT 指令。返回 True 表示成功。"""
        self.disconnect()
        new_sock = None
        try:
            new_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            new_sock.settimeout(5)
            new_sock.connect((ip, port))
            new_sock.settimeout(None)
            new_sock.sendall(pack_message(CMD_CONNECT, nickname))
            with self._lock:
                self.sock = new_sock
                self._last_heartbeat_ack = time.time()
                self._server = (ip, port)
                self.reconnect_attempts = 0
                self._disconnect_notified = False
            return True
        except Exception as e:
            print(f"[连接失败] {e}")
            try:
                new_sock.close()
            except Exception:
                pass
            with self._lock:
                self.sock = None
            return False

    def start_threads(self):
        """连接成功后调用，启动收包与心跳守护线程。"""
        if self.running:
            return
        if not self.sock:
            return
        self.running = True
        threading.Thread(target=self._recv_loop, daemon=True, name="RecvThread").start()
        threading.Thread(target=self._heartbeat_loop, daemon=True, name="Heartbeat").start()

    def disconnect(self):
        """主动断开连接，停止所有线程。"""
        with self._lock:
            self.running = False
            sock = self.sock
            self.sock = None
            self._disconnect_notified = False
        try:
            if sock:
                sock.close()
        except Exception:
            pass

    @property
    def is_connected(self) -> bool:
        return self.sock is not None and self.running

    @property
    def rtt_ms(self) -> int:
        with self._lock:
            return self._rtt_ms

    # ── 发送 ──────────────────────────────────────────
    def send_message(self, cmd: int, data: str = "") -> bool:
        """打包并发送报文。失败返回 False 并触发 on_disconnected。"""
        return self.send_raw(pack_message(cmd, data))

    def send_raw(self, data: bytes) -> bool:
        """直接发送字节。失败返回 False 并触发 on_disconnected。"""
        sock = self.sock
        if not sock:
            return False
        try:
            sock.sendall(data)
            return True
        except Exception:
            if self.running:
                self._notify_disconnect("发送失败，连接可能已断开")
            return False

    # ── 接收线程 ──────────────────────────────────────
    def _recv_loop(self):
        while self.running and self.sock:
            sock = self.sock
            if not sock:
                break
            result = recv_message(sock)
            if result is None:
                if self.running:
                    self._notify_disconnect("与服务器的连接已断开")
                break
            cmd, data = result
            if cmd == CMD_HEARTBEAT:
                now = time.time()
                with self._lock:
                    self._last_heartbeat_ack = now
                    if self._last_heartbeat_sent > 0:
                        self._rtt_ms = int((now - self._last_heartbeat_sent) * 1000)
            self._on_message(cmd, data)

    # ── 心跳线程 ──────────────────────────────────────
    def _heartbeat_loop(self):
        while self.running and self.sock:
            sock = self.sock
            if not sock:
                break
            try:
                with self._lock:
                    self._last_heartbeat_sent = time.time()
                sock.sendall(pack_message(CMD_HEARTBEAT))
            except Exception:
                if self.running:
                    self._notify_disconnect("与服务器的连接已断开")
                break
            if time.time() - self._last_heartbeat_ack > 45:
                if self.running:
                    self._notify_disconnect("心跳超时，连接已断开")
                break
            time.sleep(5)

    def _notify_disconnect(self, reason: str):
        """确保断线通知只触发一次，避免重连逻辑重入。"""
        with self._lock:
            if self._disconnect_notified:
                return
            self._disconnect_notified = True
            self.running = False
            sock = self.sock
            self.sock = None
        try:
            if sock:
                sock.close()
        except Exception:
            pass
        self._on_disconnected(reason)
