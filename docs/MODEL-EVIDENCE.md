# Recorded helper quality

The interface's automatic routing pauses a model for a role when at least four task checks from the last 30 days exist and fewer than 75% passed. Other roles remain available. Small samples and unmeasured models are not certificates of accuracy. Rechecking the same cases replaces their scores; evidence older than 30 days expires from selection. Runtime failures have a separate short backoff. Separate command-line agents retain their existing model-selection paths.

When no helper meets both the memory and recorded-quality requirements, the interface returns labeled source evidence. Paused roles and their counts are included in the helper evidence report. This policy was added after real tiny-model tests produced incomplete general answers; it does not make all accepted answers correct.
