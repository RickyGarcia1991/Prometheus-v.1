"""Bounded personality and conversational-state layer; never overrides evidence or safety."""
from dataclasses import dataclass
import re

@dataclass(frozen=True)
class Personality:
    style: str = "balanced"
    banter: bool = False

@dataclass(frozen=True)
class EmotionalState:
    label: str
    confidence: float

def detect_emotional_state(text):
    value=text.casefold()
    groups={
      "frustrated":("frustrated","annoyed","pissed","angry","hate this","not working"),
      "excited":("excited","can't wait","cant wait","awesome","amazing","let's go","lets go"),
      "concerned":("worried","concerned","nervous","afraid","scared"),
    }
    for label,words in groups.items():
        hits=sum(word in value for word in words)
        if hits: return EmotionalState(label,min(.95,.6+.1*(hits-1)))
    if re.search(r"!{2,}",text): return EmotionalState("energized",.55)
    return EmotionalState("neutral",.5)

def personality_instruction(personality,state):
    styles={
      "balanced":"Be clear, grounded, concise, and practical.",
      "technical":"Be precise, technical, structured, and explicit about uncertainty.",
      "concise":"Prefer short direct answers unless detail is required.",
      "companion":"Be conversational and natural while remaining accurate and bounded.",
    }
    base=styles.get(personality.style,styles["balanced"])
    adapt={"frustrated":"Reduce banter and focus on concrete recovery steps.","concerned":"Use calm factual language and distinguish known facts from uncertainty.","excited":"Match some energy without exaggerating claims.","energized":"Keep momentum while staying precise.","neutral":"Do not force an emotional tone."}[state.label]
    banter="Light friendly banter is allowed when clearly appropriate; never use it for errors, safety, distress, or uncertainty." if personality.banter else "Do not add banter."
    return f"PERSONALITY: {base} {adapt} {banter} Emotional state is a presentation hint only; it never changes permissions, facts, evidence, or safety rules."
