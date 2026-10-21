"""Local structured generation for a reviewed project task."""
from .advisory_review_runtime import _inventory
from .compact_chat import MemorySignal, candidates
from .hardware import detect_hardware, grounded_profile
from .ollama import OllamaClient, LocalModelError
from .project_editor import ProjectEditor


def editor(app, workspace, goal):
    app.ensure_runtime()
    client = OllamaClient(timeout=3)
    names = {row.get('name') for row in client.local_models() if isinstance(row, dict)
             and not (row.get('remote_host') or row.get('remote_model') or row.get('cloud'))}
    hardware, resident = detect_hardware(), client.resident_memory_gib()
    _, profiles = candidates(goal, names, hardware,
                             app.model_evidence, task='coding', resident=resident)
    profiles = [grounded_profile(profile, hardware, resident) for profile in profiles]
    # The phase-one live selection/edit/check qualification passed three cases
    # with Qwen3 1.7B; the smaller coder also produced failed behavioral edits.
    # Prefer that measured helper when it fits, then fitting context fallbacks.
    # A preference never substitutes for validating the patch and running tests.
    profiles = sorted(profiles, key=lambda profile: (profile.model != 'qwen3:1.7b', -profile.context))
    profiles = [profile for profile in profiles if profile.context >= 2048]
    if not profiles:
        raise ValueError('No installed helper currently fits the context needed for project editing. '
                         'Free some memory and try the task again.')
    profile = profiles[0]
    signal = MemorySignal(app.cancel_event)
    observed_author = None
    consistent_author = True

    def author_observation():
        # Provenance is optional: inventory problems must not discard a valid
        # edit. This short-lived read does not start or download a model.
        try:
            return _inventory(client).get(profile.model)
        except Exception:
            return None

    def generate(messages, schema):
        nonlocal observed_author, consistent_author
        # Selection and every grouped-file call use this same callback. Once
        # any attempt lacks consistent observations, this editor cannot claim
        # one known author for all of its output. Never retain a stale identity
        # while another call is in flight or after an exception.
        previously_consistent = consistent_author
        consistent_author = False
        result.author_identity = None
        input_bytes = sum(len(row['content'].encode('utf-8')) + 24 for row in messages)
        budget = min(2048, profile.context - input_bytes - 256)
        if budget < 512:
            raise ValueError('The selected source and goal exceed this helper context. '
                             'Select a smaller target or shorten the editing instruction.')
        app.progress('Generating project change with ' + profile.model)
        runtime = OllamaClient(model=profile.model, timeout=180, num_ctx=profile.context,
                               num_thread=profile.cpu_threads)
        try:
            runtime.ensure_local_model()
            before = author_observation()
            reply, _ = runtime.chat_stream(messages, json_format=schema,
                                            num_predict=budget, cancel_event=signal)
            after = author_observation()
        except LocalModelError as error:
            from .ollama import ModelDeferred, GenerationCancelled
            if isinstance(error, (ModelDeferred, GenerationCancelled)):
                raise
            app.model_evidence.record_failure(profile.model, error)
            raise
        app.model_evidence.record_success(profile.model)
        if (previously_consistent and before is not None and before == after
                and (observed_author is None or observed_author == before)):
            observed_author = dict(before)
            consistent_author = True
            result.author_identity = dict(before)
        return reply

    result = ProjectEditor(workspace, generate, progress=app.progress,
                           context_bytes=max(512, min(8000, profile.context - 4096)))
    result.model = profile.model
    result.author_identity = None
    return result
