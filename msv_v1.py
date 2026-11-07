import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import webbrowser
import requests
import base64
import json
import threading

API_USER = "https://api.mojang.com/users/profiles/minecraft/{username}"
API_SESSION = "https://sessionserver.mojang.com/session/minecraft/profile/{uuid}"

# 预览图最大边长（像素）。64x64 皮肤会放大 4 倍 = 256；HD 皮肤自动按整数倍缩放
PREVIEW_MAX_SIDE = 256

# 查询按钮的两种文案
BTN_IDLE_TEXT = "Look Up"
BTN_BUSY_TEXT = "Please wait..."


# ---------- 网络与解析逻辑（不涉及 UI） ----------
def fetch_skin_data(username):
    """
    通过用户名获取皮肤信息。返回 dict：
      {"ok": True, "name", "uuid", "url", "model"}
      或 {"ok": False, "error": "..."}
    """
    # 1. 用户名 -> UUID
    try:
        r = requests.get(API_USER.format(username=username), timeout=10)
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Network request failed: {e}"}

    if r.status_code == 204:
        return {"ok": False, "error": f"Player '{username}' not found."}
    if r.status_code != 200:
        return {"ok": False, "error": f"Mojang API returned HTTP {r.status_code}."}

    try:
        uuid = r.json()["id"]
    except (KeyError, json.JSONDecodeError):
        return {"ok": False, "error": "Could not parse the UUID response."}

    # 2. UUID -> 会话服务器配置文件
    try:
        r2 = requests.get(API_SESSION.format(uuid=uuid), timeout=10)
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Network request failed: {e}"}

    if r2.status_code != 200:
        return {"ok": False, "error": f"Session server returned HTTP {r2.status_code}."}

    try:
        profile = r2.json()
    except json.JSONDecodeError:
        return {"ok": False, "error": "Could not parse the profile response."}

    name = profile.get("name", username)
    props = profile.get("properties") or []
    if not props:
        return {"ok": False, "error": "This player has no texture properties (skin not set)."}

    # 3. Base64 解码 -> 提取纹理 URL 和模型类型
    try:
        textures_b64 = props[0]["value"]
        decoded = base64.b64decode(textures_b64)
        textures = json.loads(decoded)
        skin = textures.get("textures", {}).get("SKIN")
        if not skin:
            return {"ok": False, "error": "This profile contains no skin texture."}
        url = skin.get("url", "")
        # 统一使用 https，避免某些网络环境对 http 的限制
        if url.startswith("http://"):
            url = "https://" + url[len("http://"):]
        model = skin.get("metadata", {}).get("model", "classic")
        return {"ok": True, "name": name, "uuid": uuid, "url": url, "model": model}
    except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"Could not decode the texture data: {e}"}


def fetch_image_bytes(url):
    """下载皮肤 PNG 原始字节。失败返回 None。"""
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.content
    except requests.exceptions.RequestException:
        return None


# ---------- 图形界面 ----------
class SkinViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft Skin Viewer")
        self.root.geometry("360x580")
        self.root.minsize(340, 580)

        self.skin_url = None
        self.skin_bytes = None
        self.preview_img = None  # 必须保持引用，否则图像会被回收

        self._build_ui()

    # ---- UI 构建 ----
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # 查询区
        top = ttk.LabelFrame(self.root, text="Player Lookup")
        top.pack(fill="x", padx=8, pady=(8, 4))

        self.username_var = tk.StringVar()
        ttk.Label(top, text="Username:").grid(row=0, column=0, sticky="w", **pad)
        self.entry = ttk.Entry(top, textvariable=self.username_var, width=26)
        self.entry.grid(row=0, column=1, sticky="we", **pad)
        self.entry.bind("<Return>", lambda e: self.on_lookup())
        self.lookup_btn = ttk.Button(top, text=BTN_IDLE_TEXT, command=self.on_lookup)
        self.lookup_btn.grid(row=0, column=2, **pad)
        top.columnconfigure(1, weight=1)
        self.entry.focus_set()

        # 信息区
        info = ttk.LabelFrame(self.root, text="Player Info")
        info.pack(fill="x", padx=8, pady=4)

        self.name_var = tk.StringVar(value="-")
        self.uuid_var = tk.StringVar(value="-")
        self.model_var = tk.StringVar(value="-")
        self.url_var = tk.StringVar(value="-")

        ttk.Label(info, text="Name:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Label(info, textvariable=self.name_var).grid(row=0, column=1, sticky="w", **pad)
        ttk.Label(info, text="UUID:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Label(info, textvariable=self.uuid_var).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(info, text="Model:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Label(info, textvariable=self.model_var).grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(info, text="Texture URL:").grid(row=3, column=0, sticky="nw", **pad)
        self.url_entry = ttk.Entry(info, textvariable=self.url_var, state="readonly")
        self.url_entry.grid(row=3, column=1, sticky="we", **pad)
        info.columnconfigure(1, weight=1)

        # 操作按钮
        actions = ttk.Frame(self.root)
        actions.pack(fill="x", padx=8, pady=4)
        self.copy_btn = ttk.Button(actions, text="Copy URL", command=self.copy_url, state="disabled")
        self.copy_btn.pack(side="left", padx=4)
        self.download_btn = ttk.Button(actions, text="Download PNG", command=self.download_skin, state="disabled")
        self.download_btn.pack(side="left", padx=4)
        self.open_btn = ttk.Button(actions, text="Open in Browser", command=self.open_url, state="disabled")
        self.open_btn.pack(side="left", padx=4)

        # 彩蛋：默认落在窗口可见区域外（x=350），只有把窗口拉到足够宽才会露出来
        self.egg_label = ttk.Label(actions, text="By EJIAOFEN         https://github.com/EJIAOFEN/Minecraft-skin-viewer", foreground="gray")
        self.egg_label.place(x=350, rely=0.5, anchor="w")

        # 预览区：高度由图片决定
        preview_frame = ttk.LabelFrame(self.root, text="Skin Preview")
        preview_frame.pack(fill="x", padx=8, pady=4)
        self.preview_label = ttk.Label(preview_frame, text="(No skin loaded)", anchor="center")
        self.preview_label.pack(fill="x", padx=8, pady=8)

        # 状态栏
        self.status_var = tk.StringVar(value="Ready")
        status = ttk.Label(self.root, textvariable=self.status_var, relief="sunken", anchor="w")
        status.pack(fill="x", side="bottom")

    # ---- 状态切换 ----
    def _set_busy(self, busy):
        state = tk.DISABLED if busy else tk.NORMAL
        # 忙碌时按钮变灰 + 显示 "Wait..."，空闲时恢复 "Look Up"
        self.lookup_btn.config(state=state, text=BTN_BUSY_TEXT if busy else BTN_IDLE_TEXT)
        self.entry.config(state=state)

    def _reset_actions(self):
        self.copy_btn.config(state=tk.DISABLED)
        self.download_btn.config(state=tk.DISABLED)
        self.open_btn.config(state=tk.DISABLED)
        self.skin_url = None
        self.skin_bytes = None
        self.preview_img = None
        self.preview_label.config(image="", text="(No skin loaded)")

    # ---- 查询流程 ----
    def on_lookup(self):
        username = self.username_var.get().strip()
        if not username:
            messagebox.showwarning("Username Required", "Please enter a username.")
            return
        self._set_busy(True)
        self._reset_actions()
        self.status_var.set(f"Looking up '{username}'...")
        threading.Thread(target=self._lookup_worker, args=(username,), daemon=True).start()

    def _lookup_worker(self, username):
        data = fetch_skin_data(username)
        img_bytes = fetch_image_bytes(data["url"]) if data["ok"] else None
        try:
            if data["ok"]:
                self.root.after(0, self._on_lookup_done, data, img_bytes)
            else:
                self.root.after(0, self._on_lookup_failed, data["error"])
        except tk.TclError:
            pass  # 窗口已关闭

    def _on_lookup_failed(self, error):
        if not self.root.winfo_exists():
            return
        self._set_busy(False)
        self.status_var.set("Lookup failed")
        messagebox.showerror("Lookup Failed", error)

    def _on_lookup_done(self, data, img_bytes):
        if not self.root.winfo_exists():
            return
        self._set_busy(False)
        self.name_var.set(data["name"])
        self.uuid_var.set(data["uuid"])
        self.model_var.set(data["model"])
        self.url_var.set(data["url"])
        self.skin_url = data["url"]
        self.skin_bytes = img_bytes
        self.copy_btn.config(state=tk.NORMAL)
        self.open_btn.config(state=tk.NORMAL)

        if img_bytes:
            self.download_btn.config(state=tk.NORMAL)
            if self._show_preview(img_bytes):
                self.status_var.set(f"Skin loaded for {data['name']}")
            else:
                self.status_var.set(f"Metadata loaded for {data['name']}, but the preview is unavailable")
        else:
            self.preview_label.config(text="(Failed to load skin image)", image="")
            self.status_var.set(f"Metadata loaded for {data['name']}, but the image download failed")

    # ---- 预览渲染 ----
    def _show_preview(self, img_bytes):
        try:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            img = tk.PhotoImage(data=b64)
            # 以 PREVIEW_MAX_SIDE 为上限做整数倍缩放：
            #   64x64   -> x4 = 256（刚好填满预览区）
            #   128x128 -> x2 = 256
            #   256x256 -> x1 = 256
            zoom = max(1, PREVIEW_MAX_SIDE // max(1, img.width()))
            big = img.zoom(zoom) if zoom > 1 else img
            self.preview_img = big  # 保持引用
            self.preview_label.config(image=big, text="")
            return True
        except tk.TclError as e:
            self.preview_label.config(text=f"(Preview not supported: {e})", image="")
            return False

    # ---- 按钮回调 ----
    def copy_url(self):
        if self.skin_url:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.skin_url)
            self.status_var.set("Texture URL copied to clipboard")

    def open_url(self):
        if self.skin_url:
            webbrowser.open(self.skin_url)

    def download_skin(self):
        if not self.skin_bytes:
            return
        name = self.name_var.get() or "skin"
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png")],
            initialfile=f"{name}_skin.png",
        )
        if not path:
            return
        try:
            with open(path, "wb") as f:
                f.write(self.skin_bytes)
            self.status_var.set(f"Saved to {path}")
        except OSError as e:
            messagebox.showerror("Save Failed", str(e))


def main():
    root = tk.Tk()
    SkinViewerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()