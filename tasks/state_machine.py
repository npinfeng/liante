"""Canonical lifecycle shared by inference and training persistence."""
TERMINAL = frozenset({'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'REVIEW_REQUIRED'})
TRANSITIONS = {
    'PENDING': {'QUEUED', 'RUNNING', 'FAILED', 'CANCEL_REQUESTED', 'CANCELLED', 'TIMEOUT'},
    'QUEUED': {'RUNNING', 'FAILED', 'CANCEL_REQUESTED', 'CANCELLED', 'TIMEOUT'},
    'RUNNING': {'COMPLETED', 'FAILED', 'CANCEL_REQUESTED', 'CANCELLED', 'TIMEOUT', 'REVIEW_REQUIRED'},
    'CANCEL_REQUESTED': {'CANCELLED', 'FAILED', 'TIMEOUT'},
    **{status: set() for status in TERMINAL},
}

def validate_transition(current: str, target: str) -> None:
    if target == current:
        return
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f'非法任务状态转换：{current} → {target}')
