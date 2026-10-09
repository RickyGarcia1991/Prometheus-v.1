"""Bounded output checks, not a factual-accuracy or general comprehension claim."""
import json
import re

def output_issues(prompt, reply):
    if not isinstance(reply, str) or not reply.strip():
        return ("no completed response",)
    issues=[]
    text=prompt.casefold()
    if re.search(r"\b(?:one[- ]sentence|a short|a brief) explanation\b",text):
        # Equations and repeated answers alone are not an explanation.
        words=re.findall(r"\b[a-zA-Z]{2,}\b",reply)
        if len(words)<3:
            issues.append("requested explanation is missing")
    count=re.search(r"\b(?:give me|list|provide)\s+(?:exactly\s+)?(two|three|four|five|[2-9])\s+(?:examples|ideas|tips|items|reasons|steps)\b",text)
    if count:
        requested={"two":2,"three":3,"four":4,"five":5}.get(count[1],int(count[1]) if count[1].isdigit() else 0)
        items=re.findall(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S",reply)
        if len(items)!=requested:
            issues.append(f"requested {requested} list items; found {len(items)} explicit list items")
    if re.search(r"\b(?:return|respond|answer|output)\s+(?:with\s+|in\s+)?(?:only\s+|valid\s+)?json\b",text):
        try: json.loads(reply)
        except (ValueError,TypeError): issues.append("requested JSON is not valid JSON")
    lines=[re.sub(r"\s+"," ",line).strip().casefold() for line in reply.splitlines() if line.strip()]
    if len(lines)>1 and len(set(lines))==1 and "repeat" not in text:
        issues.append("response repeats the same line without additional information")
    return tuple(issues)
