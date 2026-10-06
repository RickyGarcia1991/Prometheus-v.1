# Prometheus architecture

Prometheus is the orchestration, identity, memory-policy, permission, and companion layer. Third-party engines are replaceable infrastructure.

## Boundaries
1. **Core** owns orchestration and lifecycle.
2. **Identity** owns stable companion behavior independent of model weights.
3. **Cognition** contains local model adapters; llama.cpp is a leading future candidate.
4. **Memory** owns memory policy and adapters; Qdrant is a future persistent/vector candidate.
5. **Perception** contains audio/vision input adapters; whisper.cpp and OpenCV are future candidates.
6. **Speech** contains TTS adapters.
7. **Tools** are permissioned capabilities, never arbitrary model authority.
8. **Safety** mediates privileged actions.
9. **Robotics** is isolated from cognition and requires a hardware safety/control boundary.

## Dependency rule
Core Prometheus code depends on contracts, not vendor implementations. This keeps model, vector database, speech, and robotics technologies replaceable.
