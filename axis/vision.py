"""本地 OCR、Avatar Template Matching 与可配置的 UI Layout。"""
from __future__ import annotations

from itertools import permutations
import re
from pathlib import Path

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from .counter_detection import grouped_counter_text
from .model import normalized, resolve_character, resolve_skill

DEFAULT_LAYOUT = {
    "size": [1920, 1080],
    "crop": [0, 0, 1, 1],
    "portrait_centers": [[156, 887], [442, 887], [736, 887], [999, 900], [1245, 900], [1490, 900]],
    "portrait_boxes": [[20,780,290,994],[310,780,590,994],[600,780,880,994],[904,797,1138,994],[1150,797,1382,994],[1395,797,1624,994]],
    "skill_boxes": [[15,744,300,794],[309,744,600,794],[606,744,885,794]],
    "role_boxes": [[47,809,133,852],[340,809,428,852],[630,809,718,852],[922,829,994,857],[1160,829,1240,857],[1405,829,1485,857]],
    "turn_box": [134,665,335,726],
    "bonus_box": [134,587,335,645],
    "od_box": [1583,109,1710,152],
    "button_box": [1670,844,1865,934],
}
ROLE_MAP = {"BLA":"BLA", "BLASTER":"BLA", "BRA":"BLA", "BRK":"BRK", "BREAKER":"BRK", "BUF":"BUF", "BUFF":"BUF", "BUFFER":"BUF", "BUR":"BUF", "BUFFR":"BUF", "DBF":"DBF", "DEBUFFER":"DBF", "DBFI":"DBF", "ADM":"ADM", "ADMK":"ADM", "ADMIRAL":"ADM", "DEF":"DEF", "DEFENDER":"DEF", "HEALER":"HEA", "HEA":"HEA", "ATTACKER":"ATK", "ATK":"ATK"}


def role_key(text):
    return ROLE_MAP.get(re.sub(r"[^A-Z]", "", text.upper()).rstrip("I") if text.upper().startswith("DBF") else re.sub(r"[^A-Z]", "", text.upper()), "")


def crop(frame, box):
    x1, y1, x2, y2 = map(int, box)
    return frame[y1:y2, x1:x2]


def read_image(path):
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def save_image(path, frame, quality=91):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(path.suffix, frame, [cv2.IMWRITE_JPEG_QUALITY, quality] if path.suffix.lower() in (".jpg", ".jpeg") else [])[1].tofile(str(path))


def parse_counter(text, keyword):
    clean = re.sub(r"[\s|]", "", text.upper()).replace("O", "0")
    token = keyword.upper().replace("O", "0")
    if token not in clean:
        return None
    tail = clean.split(token, 1)[1].replace("\\", "/").replace(".", "/")
    m = re.search(r"(\d{1,3})/(\d{1,3})", tail)
    if m:
        a, b = map(int, m.groups())
        if 1 <= a <= b <= 999:
            return [a, b]
    # Slash 被 OCR 读成 1 时，只接受唯一符合 a <= b 的 Counter。
    if tail.isdecimal() and 3<=len(tail)<=7:
        possibilities=set()
        for index in range(1,len(tail)-1):
            if tail[index]=="1":
                a,b=int(tail[:index]),int(tail[index+1:])
                if 1<=a<=b<=999:possibilities.add((a,b))
        if len(possibilities)==1:return list(possibilities.pop())
    return None


def parse_turn(text):
    """Read a labeled TURN integer or fraction, without scene-number guesses."""
    if not isinstance(text, str):
        return None
    clean = re.sub(r"[\s|]", "", text.upper()).replace("O", "0")
    match = re.fullmatch(r"TURN(\d{1,3})", clean)
    if match:
        value = int(match[1])
        return value if 1 <= value <= 999 else None
    # Keep explicit Counter separators; a missing slash is ambiguous with TURN111.
    if re.fullmatch(r"TURN\d{1,3}[/\\.]\d{1,3}", clean):
        counter = parse_counter(clean, "TURN")
        return counter[0] if counter else None
    # Retain the previous unique OCR repair for a slash read as 1.
    if re.fullmatch(r"TURN\d{4,7}", clean):
        counter = parse_counter(clean, "TURN")
        return counter[0] if counter else None
    return None


class Video:
    def __init__(self, path, layout=None):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise ValueError("视频不存在，请选择有效的 Video 文件。")
        self.layout = {**DEFAULT_LAYOUT, **(layout or {})}
        self.cap = cv2.VideoCapture(str(self.path))
        if not self.cap.isOpened():
            raise ValueError("无法解码此 Video，请转换为常见的 MP4 / H.264 后重试。")
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.duration = self.frames / self.fps if self.fps > 0 else 0
        if not self.duration:
            self.close()
            raise ValueError("无法读取 Video 的 FPS 或时长。")
        self.width, self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def normalize(self, frame):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = self.layout["crop"]
        area = frame[round(y1*h):round(y2*h), round(x1*w):round(x2*w)]
        if area.size == 0:
            raise ValueError("游戏画面的 Crop 范围无效。")
        return cv2.resize(area, tuple(self.layout["size"]))

    def frame(self, seconds):
        self.cap.set(cv2.CAP_PROP_POS_MSEC, max(0, min(seconds, self.duration-.05))*1000)
        ok, frame = self.cap.read()
        if not ok:
            raise ValueError(f"无法读取 {seconds:.2f}s 的 Frame。")
        return self.normalize(frame)

    def sample(self, start=0, end=None, rate=4):
        finish = min(self.duration, self.duration if end is None else end)
        number = round(start*self.fps)
        stride = max(1, round(self.fps/rate))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, number)
        while number / self.fps < finish and self.cap.grab():
            if (number - round(start*self.fps)) % stride == 0:
                ok, frame = self.cap.retrieve()
                if not ok:
                    break
                yield number/self.fps, self.normalize(frame)
            number += 1

    def metadata(self):
        return {"path":str(self.path),"name":self.path.name,"width":self.width,"height":self.height,"fps":self.fps,"duration":self.duration,"frames":self.frames}

    def close(self):
        self.cap.release()


class Reader:
    def __init__(self, layout=None, aliases=None):
        self.layout = {**DEFAULT_LAYOUT, **(layout or {})}
        self.aliases = aliases or {}
        self.ocr = RapidOCR(intra_op_num_threads=4, inter_op_num_threads=1, text_score=.55)

    def lines(self, patches):
        return self.ocr.text_rec(patches)[0]

    def status(self, frame):
        result = self.lines([crop(frame, self.layout[k]) for k in ["turn_box","bonus_box","od_box"]])
        turn, bonus, od = result
        turn_value = parse_turn(turn[0]) if turn[1] >= .6 else None
        extra = parse_counter(bonus[0], "BONUS") if bonus[1] >= .62 else None
        # Some UI versions place BONUS in the TURN area; recognition-only OCR
        # also merges labels, large numerals and slash glyphs against scenery.
        same_area_bonus=parse_counter(turn[0],"BONUS") if turn[1]>=.62 else None
        if same_area_bonus:
            extra=same_area_bonus
            bonus=turn
        sources={"turn":"recognition","bonus":"recognition"}
        if hasattr(self,"ocr") and (turn_value is None or turn_value>99 or
                                    (extra is None and "ON" in bonus[0].upper())):
            areas=[self.layout["turn_box"],self.layout["bonus_box"]]
            x1=max(0,min(b[0] for b in areas)-10)
            y1=max(0,min(b[1] for b in areas)-25)
            x2=min(frame.shape[1],max(b[2] for b in areas)+25)
            y2=min(frame.shape[0],max(b[3] for b in areas)+10)
            detected,_=self.ocr(frame[y1:y2,x1:x2],use_cls=False,text_score=.6)
            for keyword in ("TURN","BONUS"):
                candidate=grouped_counter_text(detected,keyword)
                if not candidate:
                    continue
                text,confidence=candidate
                parsed=parse_turn(text) if keyword=="TURN" else parse_counter(text,keyword)
                if parsed is None or confidence<.65:
                    continue
                if keyword=="TURN":
                    turn_value,turn=parsed,candidate
                    sources["turn"]="detection"
                else:
                    extra,bonus=parsed,candidate
                    sources["bonus"]="detection"
        match = re.fullmatch(r"[^\d+\-]*([+\-]?\d{1,3}(?:\.\d+)?)\s*[%％][^\d+\-]*", od[0])
        value = float(match[1]) if match and od[1] >= .65 else None
        return {"turn":turn_value,"bonus":extra,"od":value,
                "raw":{"turn":turn[0],"bonus":bonus[0],"od":od[0]},
                "confidence":{"turn":float(turn[1]),"bonus":float(bonus[1]),"od":float(od[1])},"sources":sources}

    def roles(self, frame):
        return [role_key(text) if score >= .6 else "" for text,score in self.lines([crop(frame,b) for b in self.layout["role_boxes"]])]

    def od_menu(self,frame):
        text,confidence=self.lines([frame[903:995,855:1090]])[0]
        return confidence>.65 and ("取消" in text or "cancel" in text.casefold())

    def skills(self, frame):
        # DBNet 单次检测整条 Skill Label；不把场景背景的字符猜为 A。
        boxes = self.layout["skill_boxes"]
        x1, y1 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x2, y2 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        result, _ = self.ocr(frame[y1:y2,x1:x2],use_cls=False,text_score=.55)
        items = [[] for _ in range(3)]
        for bbox,text,confidence in result or []:
            center = np.array(bbox).mean(axis=0) + [x1,y1]
            if text.upper() in ("DAMAGE", "SP", "DP", "HP") or text.isdecimal():
                continue
            for i,(a,b,c,d) in enumerate(boxes):
                if a <= center[0] <= c and b <= center[1] <= d:
                    items[i].append((bbox[0][0],text,float(confidence)))
                    break
        actions=[]
        direct=self.lines([crop(frame,b) for b in boxes])
        for index,item in enumerate(items):
            raw = "".join(x[1] for x in sorted(item))
            raw = re.sub(r"^[\d.,%\s]+(?=[\u4e00-\u9fff])", "", raw)
            score = min([x[2] for x in item], default=1.)
            line,line_confidence=direct[index]
            line=re.sub(r"^[\d.,%\s]+(?=[\u4e00-\u9fff])", "",line)
            plausible_line=len(re.findall(r"[A-Za-z\u3040-\u30ff\u3400-\u9fff]",line))>=2 and line.upper() not in ("DAMAGE","SP","DP","HP")
            detected_raw=raw
            disagreement=bool(raw and plausible_line and line_confidence>=.73 and normalized(raw)!=normalized(line))
            if not raw and plausible_line and line_confidence>=.6:
                raw,score=line,float(line_confidence)
            elif raw and len(normalized(raw))<len(normalized(line)) and line_confidence>=.73 and normalized(raw) in normalized(line):
                # Longer recognition-only text can contain scenery or a stray
                # ASCII prefix/suffix. Keep it as Evidence, not a silent repair.
                added=normalized(line).replace(normalized(raw),"",1)
                if re.fullmatch(r"[\u3040-\u30ff\u3400-\u9fff]+",added):
                    raw,score=line,float(line_confidence)
            full, alias, match_score, reason = resolve_skill(raw,self.aliases)
            actions.append({"skill_raw":raw,"skill_full":full,"skill":alias,
                            "skill_confidence":round(min(score,match_score),3),"skill_note":reason,
                            "skill_truncated":bool(re.search(r"(?:\.{2,}|…+|⋯+)$",raw.strip())),
                            "skill_ocr_disagreement":disagreement,
                            "skill_ocr_alternatives":{"detection":detected_raw,"recognition":line},
                            "skill_suspicious":bool(re.search(r"[#�]",raw) or re.fullmatch(r"[\u3400-\u9fff]",raw.strip()))})
        return actions

    def roster(self, frame):
        # 队伍配置画面的 6 张卡。缺少此画面时用 Avatar ID，允许在 GUI 命名。
        roles = self.lines([frame[557:607,130+i*289:378+i*289] for i in range(6)])
        if sum(bool(role_key(t)) and c>.7 for t,c in roles) < 5:
            return None
        names = self.lines([frame[507:562,130+i*289:378+i*289] for i in range(6)])
        return [{**resolve_character(text),"name_confidence":float(conf)} if conf > .62 and len(text)>=2 else {"name":f"角色 {i+1}","alias":f"角色 {i+1}","name_confidence":0.,"name_raw":text,"name_source":"placeholder","name_is_placeholder":True} for i,(text,conf) in enumerate(names)]


class Avatars:
    def __init__(self, frame, layout=None, roles=None):
        self.layout = {**DEFAULT_LAYOUT, **(layout or {})}
        self.roles = roles or [""]*6
        self.images, self.templates = [], []
        self.previous = list(range(6))
        gray = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        for x,y in self.layout["portrait_centers"]:
            image = frame[y-15:y+57,x-39:x+39]
            self.images.append(image.copy())
            face = gray[y-15:y+57,x-39:x+39]
            self.templates.append([cv2.resize(face,(round(78*.5*k),round(72*.5*k))) for k in [.78,.85,1.,1.18,1.25]])
        self.button = cv2.resize(crop(gray,self.layout["button_box"]),(98,45))

    def button_state(self, frame):
        patch=crop(frame,self.layout["button_box"])
        hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
        pink=float(((hsv[:,:,0]>136)&(hsv[:,:,0]<179)&(hsv[:,:,1]>65)&(hsv[:,:,2]>105)).mean())
        grey=float((hsv[:,:,1]<50).mean())
        gray=cv2.resize(cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY),(98,45))
        score=float(cv2.matchTemplate(gray,self.button,cv2.TM_CCOEFF_NORMED)[0,0])
        return pink>.5 and score>.70, grey>.80 and score>.70, score

    def availability(self,frame):
        hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
        scores=[]
        for index,(x,y) in enumerate(self.layout["portrait_centers"][:3]):
            radii=self.layout.get("portrait_radii")
            rx,ry=radii[index] if radii else (100,100)
            dx,dy=round(rx*1.15),round(ry*1.15)
            left,top=max(0,x-dx),max(0,y-dy)
            patch=hsv[top:min(hsv.shape[0],y+dy),left:min(hsv.shape[1],x+dx)]
            yy,xx=np.indices(patch.shape[:2])
            distance=((xx-(x-left))/rx)**2+((yy-(y-top))/ry)**2
            ring=(distance>.85**2)&(distance<1.05**2)
            pink=(patch[:,:,0]>135)&(patch[:,:,0]<177)&(patch[:,:,1]>75)&(patch[:,:,2]>110)
            scores.append(round(float(pink[ring].mean()),3))
        return scores

    @staticmethod
    def od_menu(frame):
        middle=cv2.resize(frame[290:700,120:1810],(845,205))
        gray=cv2.cvtColor(middle,cv2.COLOR_BGR2GRAY)
        circles=cv2.HoughCircles(gray,cv2.HOUGH_GRADIENT,1.4,105,param1=100,param2=35,minRadius=35,maxRadius=64)
        if circles is None:return False
        candidates=circles[0]
        for y in candidates[:,1]:
            aligned=[c for c in candidates if abs(float(c[1])-float(y))<10]
            if len(aligned)>=4 and max(c[0] for c in aligned)-min(c[0] for c in aligned)>450:
                return True
        return False

    def colored_slots(self, small):
        hsv = cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
        color = (hsv[:,:,0]>130)&(hsv[:,:,0]<179)&(hsv[:,:,1]>65)&(hsv[:,:,2]>105)
        return sum(float(crop(color,[v//2 for v in b]).mean()) > .06 for b in self.layout["portrait_boxes"])

    def match(self, small):
        gray = cv2.cvtColor(small,cv2.COLOR_BGR2GRAY)
        scores = np.zeros((6,6),dtype=np.float32)
        for slot,b in enumerate(self.layout["portrait_boxes"]):
            patch = crop(gray,[v//2 for v in b])
            for ident,variants in enumerate(self.templates):
                scores[slot,ident] = max(cv2.minMaxLoc(cv2.matchTemplate(patch,t,cv2.TM_CCOEFF_NORMED))[1] for t in variants)
        strong = [(int(row.argmax()),float(row.max())) for row in scores]
        identities = [ident for ident,score in strong if score >= .80]
        present = len(identities)>=2 and len(set(identities))>=2
        button_patch = cv2.resize(crop(gray,[v//2 for v in self.layout["button_box"]]),(98,45))
        button_score = float(cv2.matchTemplate(button_patch,self.button,cv2.TM_CCOEFF_NORMED)[0,0])
        return present, scores, button_score

    def assign(self, scores, roles=None):
        adjusted = scores.copy()
        for slot in range(6):
            adjusted[slot,self.previous[slot]] += .025
            if roles and roles[slot]:
                for ident in range(6):
                    if self.roles[ident]:
                        adjusted[slot,ident] += .35 if roles[slot]==self.roles[ident] else -.35
        order = max(permutations(range(6)),key=lambda p:sum(adjusted[i,j] for i,j in enumerate(p)))
        confidence=[]
        for slot,ident in enumerate(order):
            score=float(scores[slot,ident])
            if roles and roles[slot] and self.roles.count(roles[slot])==1 and roles[slot]==self.roles[ident]:
                score=max(score,.88)
            confidence.append(round(score,3))
        uncertain=[i for i,s in enumerate(confidence) if s<.78]
        if len(uncertain)==1:
            slot=uncertain[0]
            if all(c>=.83 for i,c in enumerate(confidence) if i!=slot) and roles and roles[slot] and roles[slot]==self.roles[order[slot]] and confidence[slot]>.5:
                confidence[slot]=.84
        self.previous = list(order)
        return list(order),confidence
