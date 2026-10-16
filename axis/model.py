"""行动轴的数据结构、简写和 Turn / OD State Machine。"""
from __future__ import annotations

import math
import re
import unicodedata

SCHEMA_VERSION = 1
ALIASES = {
    "普通攻擊": "A", "普通攻击": "A", "Normal Attack": "A", "Attack": "A",
    "通常攻撃": "A", "普通攻撃": "A",
}


def normalized(text):
    variants="帶带熱热雜杂燴烩點点毀毁勝胜獻献載载揮挥鄉乡奮奋倉仓憐怜階阶聖圣碼码夢梦輝辉優优雙双國国無无氣气殺杀爾尔結结華华補补軟软風风體体滅灭暗暗舞舞"
    table={ord(variants[i]):variants[i+1] for i in range(0,len(variants),2)}
    return re.sub(r"[\s\-·・!！.。…:：]", "", unicodedata.normalize("NFKC", text)).translate(table).casefold()


def resolve_skill(text, aliases=None):
    """任意 Skill 保留 OCR 原文；只有显式 Alias 的 Exact Match 才使用简写。"""
    catalog = {**ALIASES, **(aliases or {})}
    key = normalized(text)
    if not key:
        return "普通攻擊", "A", 1.0, "未选择 Skill，默认 A"
    if key in {normalized(t) for t in ("追加回合","追加回","追加ターン","Extra Turn","Additional Turn","—","不行动")}:
        return text, "—", 1.0, "该位置不能在本次追加回合行动"
    for full, alias in catalog.items():
        if normalized(full) == key:
            return full, alias, 1.0, ""
    return text, text, 1.0, "未设置简写，保留 OCR 原文"


def resolve_character(text):
    """配置画面可选 OCR：保留任意名字，不从固定角色列表猜测。"""
    name=text.strip()
    return {"name":name,"alias":name,"name_confidence":.6,"name_raw":text,"name_source":"roster_ocr","name_is_placeholder":False}


def build_team(supplied=None):
    """用显式 Profile 或 6 个 Placeholder 创建与 Avatar Identity 独立的名称。"""
    team=[]
    for index in range(6):
        person=supplied[index] if supplied else {}
        name=(person.get("name") or person.get("alias") or "").strip()
        alias=(person.get("alias") or name).strip()
        placeholder=not name or bool(person.get("name_is_placeholder"))
        label=f"角色 {index+1}"
        team.append({"name":name or label,"alias":alias or label,"name_confidence":0. if placeholder else 1.,"name_raw":"" if placeholder else "用户配置","name_source":"placeholder" if placeholder else "profile","name_is_placeholder":placeholder})
    return team


def stamp(seconds):
    minutes, rest = divmod(max(0.0, float(seconds)), 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


def turn_coverage(rows):
    """Describe numbered gaps without inventing Turns, Actions or Skills."""
    gaps=[]
    for index,(after,before) in enumerate(zip(rows,rows[1:]),1):
        first,last=after.get("turn"),before.get("turn")
        if type(first) is not int or type(last) is not int or not(1<=first<last<=999) or last-first<=1:
            continue
        missing=list(range(first+1,last))
        after_id=after.get("id") or f"row-{index}"
        before_id=before.get("id") or f"row-{index+1}"
        start,end=after.get("confirm_time"),before.get("phase_start")
        def valid_time(value):
            return float(value) if type(value) in (int,float) and math.isfinite(value) and value>=0 else None
        numbered=str(missing[0]) if len(missing)==1 else f"{missing[0]}–{missing[-1]}"
        gaps.append({"id":f"turn-gap-{after_id}-{before_id}","after_turn":first,"before_turn":last,
                     "missing_turns":missing,"after_row_id":after_id,"before_row_id":before_id,
                     "start_time":valid_time(start),"end_time":valid_time(end),
                     "message":f"TURN {numbered} 未提取到选招/确认记录，请核对原视频；未补写 Skill。"})
    return {"status":"gaps_detected" if gaps else "no_numbered_gaps_detected",
            "missing_turn_count":sum(len(gap["missing_turns"]) for gap in gaps),"gaps":gaps}


def _coverage_warning(coverage):
    return (f"行动轴存在 {len(coverage['gaps'])} 处 TURN 缺口，共 {coverage['missing_turn_count']} 个回合编号没有选招/确认记录；"
            "未补写 Skill，请核对原视频。")


def annotate_coverage(document):
    """Attach derived Coverage in memory; preserve Actions and manual review data."""
    rows=document.get("rows",[])
    previous=document.get("coverage")
    if isinstance(previous,dict) and isinstance(previous.get("gaps"),list):
        old_messages={gap.get("message") for gap in previous["gaps"] if isinstance(gap,dict) and isinstance(gap.get("message"),str)}
        for row in rows:
            if isinstance(row.get("notes"),list):row["notes"]=[note for note in row["notes"] if note not in old_messages]
        if isinstance(document.get("warnings"),list) and isinstance(previous.get("missing_turn_count"),int):
            warning=_coverage_warning(previous)
            document["warnings"]=[item for item in document["warnings"] if item!=warning]
    coverage=turn_coverage(rows)
    document["coverage"]=coverage
    by_id={row.get("id") or f"row-{index}":row for index,row in enumerate(rows,1)}
    for gap in coverage["gaps"]:
        row=by_id[gap["before_row_id"]]
        row["needs_review"]=True
        notes=row.get("notes")
        if not isinstance(notes,list):notes=[notes] if isinstance(notes,str) and notes else []
        if gap["message"] not in notes:notes.append(gap["message"])
        row["notes"]=notes
    if coverage["gaps"]:
        warnings=document.get("warnings")
        if not isinstance(warnings,list):warnings=[]
        warning=_coverage_warning(coverage)
        if warning not in warnings:warnings.append(warning)
        document["warnings"]=warnings
    return document


def annotate_rows(rows):
    """用实际确认顺序和 TURN / BONUS Counter 判断 OD 时机。

    同一个 Normal Turn 的重复确认是追加回合。前置 OD 的 BONUS
    先于该 Normal Turn 确认；后置 OD 的 BONUS 在该 Turn 确认之后。
    """
    def flag_unknown(row,message):
        notes=row.get("notes")
        if not isinstance(notes,list):notes=[notes] if isinstance(notes,str) and notes else []
        if message not in notes:notes.append(message)
        row["notes"]=notes
        row["needs_review"]=True

    normal_seen, session, last_bonus, extra_count = set(), 0, None, 0
    for i, row in enumerate(rows):
        turn = row.get("turn")
        bonus = row.get("bonus")
        row["od_session"] = None
        if bonus:
            current = (turn, bonus[0], bonus[1])
            fresh = (last_bonus is None or turn != last_bonus[0] or
                     (bonus[0] == 1 and bonus[0] < last_bonus[1]) or
                     row.get("od_activation", False))
            if fresh:
                session += 1
                extra_count = 0
            if turn is None:
                row["kind"],row["label"]="od","OD（时机待核对）"
                row["od_timing"]="unknown"
                flag_unknown(row,"TURN Counter 不可读，无法判定前置或后置 OD；请核对。")
            elif fresh and bonus[0]!=1 and not row.get("od_activation"):
                row["kind"],row["label"]="od","OD（起点待核对）"
                row["od_timing"]="unknown"
                flag_unknown(row,"首次可读 BONUS 不是 1，无法判定 OD 起点或时机；请核对。")
            elif fresh:
                row["kind"] = "post_od" if turn in normal_seen else "pre_od"
                row["label"] = f"{'后置' if row['kind'] == 'post_od' else '前置'}OD{row.get('od_level') or bonus[1]}"
                row["od_timing"] = "post" if row["kind"] == "post_od" else "pre"
            elif current[:2] == last_bonus[:2]:
                extra_count += 1
                row["kind"], row["label"] = "extra", "追加"
            else:
                row["kind"], row["label"] = "od", "OD内"
            row["od_session"] = session
            row["bonus_text"] = f"BONUS {bonus[0]}/{bonus[1]}"
            last_bonus = current
        else:
            row["bonus_text"] = ""
            if row.get("bonus_unreadable"):
                row["kind"],row["label"]="od","OD（Counter 待核对）"
                row["od_timing"]="unknown"
                flag_unknown(row,"BONUS Counter 不可读，无法判定 OD 时机或追加；请核对。")
                if last_bonus and (turn is None or turn==last_bonus[0]):
                    row["od_session"]=session
            elif turn is None:
                row["kind"],row["label"]="normal","待核对"
                row.pop("od_timing",None)
                flag_unknown(row,"TURN Counter 不可读，回合编号待核对；未将未知回合合并为追加。")
            elif turn in normal_seen:
                row["kind"], row["label"] = "extra", "追加"
            else:
                normal_seen.add(turn)
                row["kind"], row["label"] = "normal", str(turn) if turn is not None else "待核对"
            if not row.get("bonus_unreadable"):
                last_bonus = None
        row["index"] = i + 1
    for number in range(1, session + 1):
        group = [r for r in rows if r.get("od_session") == number]
        if group:
            group[0]["od_action_count"] = len(group)
    return rows


def format_skill_sequence(sequence):
    """把连续重复的 Skill 显示为 ×2，保留先后顺序。"""
    groups=[]
    for value in sequence:
        if groups and groups[-1][0]==value:groups[-1][1]+=1
        else:groups.append([value,1])
    return " + ".join(f"{value}×{count}" if count>1 else value for value,count in groups)


def compact_rows(rows):
    """合并追加确认，保持 Character Identity，避免换位后拼错 Skill。"""
    result = []
    for row in rows:
        if row.get("kind") == "extra" and row.get("turn") is not None and result and row.get("turn") == result[-1].get("turn") and row.get("od_session") == result[-1].get("od_session"):
            previous = result[-1]
            by_id = {a["character_id"]: a for a in previous["actions"]}
            for action in row["actions"]:
                if action.get("skill") == "—":
                    continue
                if action["character_id"] in by_id:
                    target = by_id[action["character_id"]]
                    target["skill_sequence"].append(action["skill"])
                    target["skill"] = format_skill_sequence(target["skill_sequence"])
                    target["skill_full"] += " + " + action["skill_full"]
                else:
                    previous["extra_actions"].append(action.copy())
            previous["confirmations"].append(row.get("confirm_time", row["time"]))
            previous["needs_review"] |= row.get("needs_review", False)
            previous["reviewed"] = bool(previous.get("reviewed")) and bool(row.get("reviewed"))
            previous["od_after"] = row.get("od_after")
        else:
            result.append({**row, "actions": [{**a,"skill_sequence":[a["skill"]]} for a in row["actions"]], "extra_actions": [], "confirmations": [row.get("confirm_time", row["time"])]})
    return result
