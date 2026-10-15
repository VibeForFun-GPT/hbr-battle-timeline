"""Windows game-window capture with an auditable local Video buffer."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import threading
import time

import cv2
import numpy as np
from PIL import ImageGrab


class CapturePaused(Exception):
    pass


def windows_api():
    if os.name != "nt":
        raise ValueError("实时捕获目前支持 Windows；仍可使用录像导入。")
    api=ctypes.WinDLL("user32",use_last_error=True)
    declarations={
        "IsWindow":([wintypes.HWND],wintypes.BOOL),
        "IsWindowVisible":([wintypes.HWND],wintypes.BOOL),
        "IsIconic":([wintypes.HWND],wintypes.BOOL),
        "GetClientRect":([wintypes.HWND,ctypes.POINTER(wintypes.RECT)],wintypes.BOOL),
        "ClientToScreen":([wintypes.HWND,ctypes.POINTER(wintypes.POINT)],wintypes.BOOL),
        "GetWindowTextLengthW":([wintypes.HWND],ctypes.c_int),
        "GetWindowTextW":([wintypes.HWND,wintypes.LPWSTR,ctypes.c_int],ctypes.c_int),
        "GetWindowThreadProcessId":([wintypes.HWND,ctypes.POINTER(wintypes.DWORD)],wintypes.DWORD),
        "GetForegroundWindow":([],wintypes.HWND),
        "GetAncestor":([wintypes.HWND,wintypes.UINT],wintypes.HWND),
        "GetSystemMetrics":([ctypes.c_int],ctypes.c_int),
        "GetAsyncKeyState":([ctypes.c_int],ctypes.c_short),
    }
    for name,(args,result) in declarations.items():
        method=getattr(api,name);method.argtypes=args;method.restype=result
    if hasattr(api,"SetThreadDpiAwarenessContext"):
        api.SetThreadDpiAwarenessContext.argtypes=[wintypes.HANDLE]
        api.SetThreadDpiAwarenessContext.restype=wintypes.HANDLE
        api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    return api


def window_info(hwnd,api=None):
    api=api or windows_api()
    if not api.IsWindow(hwnd):raise ValueError("游戏窗口已关闭，请刷新窗口列表。")
    length=min(2048,api.GetWindowTextLengthW(hwnd))
    title=ctypes.create_unicode_buffer(length+1)
    api.GetWindowTextW(hwnd,title,length+1)
    rect=wintypes.RECT();point=wintypes.POINT(0,0);pid=wintypes.DWORD()
    if not api.GetClientRect(hwnd,ctypes.byref(rect)) or not api.ClientToScreen(hwnd,ctypes.byref(point)):
        raise ValueError("无法读取游戏窗口的位置。")
    api.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    return {"hwnd":int(hwnd),"pid":int(pid.value),"title":title.value,"left":point.x,"top":point.y,
            "width":rect.right,"height":rect.bottom,"minimized":bool(api.IsIconic(hwnd))}


def list_windows():
    api=windows_api();items=[]
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def visit(hwnd,_):
        if api.IsWindowVisible(hwnd):
            try:
                info=window_info(hwnd,api)
                if info["title"] and info["width"]>=320 and info["height"]>=180:items.append(info)
            except ValueError:pass
        return True
    callback=callback_type(visit)
    api.EnumWindows.argtypes=[callback_type,wintypes.LPARAM];api.EnumWindows.restype=wintypes.BOOL
    api.EnumWindows(callback,0)
    items.sort(key=lambda w:w["title"].casefold())
    return items


class WindowSource:
    def __init__(self,hwnd):
        if type(hwnd) is not int or hwnd<=0:raise ValueError("请选择游戏窗口。")
        self.api=windows_api();self.info=window_info(hwnd,self.api)
        if self.info["width"]<320 or self.info["height"]<180:raise ValueError("游戏窗口太小，请恢复或放大窗口。")
        self.hwnd=hwnd

    def frame(self):
        if hasattr(self.api,"SetThreadDpiAwarenessContext"):
            self.api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        info=window_info(self.hwnd,self.api)
        if info["pid"]!=self.info["pid"]:raise ValueError("原游戏窗口已关闭，请重新选择。")
        if info["minimized"]:raise CapturePaused("游戏已最小化，等待恢复窗口")
        foreground=self.api.GetForegroundWindow()
        if self.api.GetAncestor(foreground,2)!=self.api.GetAncestor(self.hwnd,2):
            raise CapturePaused("等待切回游戏窗口")
        left,top,width,height=(info[k] for k in ("left","top","width","height"))
        x,y,w,h=(self.api.GetSystemMetrics(k) for k in (76,77,78,79))
        if width<320 or height<180 or left<x or top<y or left+width>x+w or top+height>y+h:
            raise CapturePaused("请将完整游戏窗口放在屏幕内")
        image=ImageGrab.grab(bbox=(left,top,left+width,top+height),all_screens=True)
        # Check again after the grab so an Alt+Tab cannot save another app.
        if self.api.GetAncestor(self.api.GetForegroundWindow(),2)!=self.api.GetAncestor(self.hwnd,2):
            raise CapturePaused("等待切回游戏窗口")
        return cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2BGR)

    def stop_requested(self):
        return all(self.api.GetAsyncKeyState(key)&0x8000 for key in (0x11,0x12,0x45))


class CaptureSession:
    def __init__(self,folder,source,*,fps=8,progress=None,finished=None):
        if type(fps) not in (int,float) or not 4<=fps<=12:raise ValueError("捕获精度需要为 4–12 FPS。")
        self.folder=Path(folder);self.source=source;self.fps=float(fps)
        self.progress=progress;self.finished=finished;self.stop_event=threading.Event()
        self.lock=threading.RLock();self.latest=None;self.video=self.folder/"capture.webm"
        self.stats={"active":True,"state":"waiting","frames":0,"duration":0.,"paused_seconds":0.,"samples":0,
                    "message":"请切回游戏窗口，捕获将自动开始","window":dict(source.info),"fps":self.fps}
        self.thread=threading.Thread(target=self._run,name="hbr-window-capture",daemon=True)

    def snapshot(self):
        with self.lock:return {**self.stats,"window":dict(self.stats["window"])}

    def frame(self):
        with self.lock:
            if self.latest is None:raise ValueError("尚未捕获到游戏画面，请先切回游戏窗口。")
            return self.latest.copy()

    def start(self):self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.lock:self.stats.update(state="stopping",message="正在结束捕获并生成行动轴")

    def _run(self):
        writer=None;last_frame=None;started=None;frames=0;samples=0;paused=0.;last_report=0.;error=None
        last_tick=time.monotonic();deadline=last_tick
        try:
            while not self.stop_event.is_set():
                if self.source.stop_requested():self.stop_event.set();break
                message="正在捕获游戏窗口";state="capturing"
                try:frame=self.source.frame()
                except CapturePaused as reason:
                    frame=None;message=str(reason);state="paused" if started is not None else "waiting"
                now=time.monotonic()
                if now-last_tick>5 and started is not None:
                    raise ValueError("系统长时间休眠或捕获中断。已保留捕获素材，可以通过录像导入重新分析。")
                if frame is not None:
                    if writer is None:
                        height,width=frame.shape[:2];scale=min(1,1920/width,1080/height)
                        size=(max(2,int(width*scale)//2*2),max(2,int(height*scale)//2*2))
                        self.folder.mkdir(parents=True,exist_ok=True)
                        writer=cv2.VideoWriter(str(self.video),cv2.VideoWriter_fourcc(*"VP80"),self.fps,size)
                        if not writer.isOpened():raise ValueError("无法创建本地捕获素材，请检查输出目录。")
                        started=now
                    frame=cv2.resize(frame,size,interpolation=cv2.INTER_AREA)
                    samples+=1
                elif started is not None:paused+=now-last_tick
                if started is not None:
                    target=int((now-started)*self.fps)+1
                    # Duplicate only the last game frame while paused; never capture the desktop.
                    while frames<target-1:
                        writer.write(last_frame);frames+=1
                    if frames<target:
                        writer.write(frame if frame is not None else last_frame);frames+=1
                    if frame is not None:
                        last_frame=frame
                        with self.lock:self.latest=frame.copy()
                with self.lock:
                    self.stats.update(state=state,message=message,frames=frames,samples=samples,
                                      duration=round(frames/self.fps,2),paused_seconds=round(paused,2))
                if self.progress and now-last_report>=.7:
                    self.progress(self.snapshot());last_report=now
                last_tick=now;deadline+=1/self.fps
                if deadline<now-1:deadline=now
                if self.stop_event.wait(max(0,deadline-time.monotonic())):break
            if frames<2:raise ValueError("没有捕获到足够的游戏画面，请开始后切回游戏窗口。")
        except Exception as reason:error=str(reason)
        finally:
            if writer is not None:writer.release()
            with self.lock:self.stats.update(active=False,state="error" if error else "finished",message=error or "捕获已结束，正在识别")
            if self.finished:self.finished(self.video,self.snapshot(),error)
