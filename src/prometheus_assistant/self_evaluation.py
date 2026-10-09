"""Deterministic, inspectable self-evaluation for completed agent cycles."""
from dataclasses import dataclass
from .response_checks import output_issues

@dataclass(frozen=True)
class SelfEvaluation:
    authorization: float
    execution: float
    evidence: float
    response: float
    overall: float
    passed: bool
    reasons: tuple[str,...]
    scope: str = "execution_and_supported_output_checks"
    factual_accuracy: str = "not_verified"

def evaluate_agent_result(core):
    reasons=[]
    authorization=1.0 if core.evaluation.authorization_ok else 0.0
    if not authorization: reasons.append("authorization failed")
    statuses=[item.status for item in core.evidence]
    execution=1.0 if all(s=="complete" for s in statuses) else (0.5 if not statuses else 0.0)
    if statuses and execution==0: reasons.append("one or more tools did not complete")
    evidence=1.0 if core.evaluation.tool_accuracy==1.0 else 0.0
    if evidence<1: reasons.append("executed tools differed from the authorized plan")
    response=1.0 if isinstance(core.reply,str) and core.reply.strip() else 0.0
    if response==0: reasons.append("no completed response")
    problems=output_issues(core.plan.prompt,core.reply)
    if problems:
        response=0.0
        reasons.extend(problem for problem in problems if problem not in reasons)
    prompt=core.plan.prompt.casefold()
    grounding=1.0
    system_claim=any(word in prompt for word in ("version","running","current status","system status","core status","operating system","hardware","ram","memory available","cpu","processor","resources available","computer resources"))
    if system_claim and not statuses:
        grounding=0.0; reasons.append("current system/hardware/status claim lacks tool evidence")
    overall=round((authorization+execution+evidence+response+grounding)/5,3)
    passed=bool(core.evaluation.passed and response==1.0 and authorization==1.0 and evidence==1.0 and grounding==1.0)
    return SelfEvaluation(authorization,execution,evidence,response,overall,passed,tuple(reasons))
