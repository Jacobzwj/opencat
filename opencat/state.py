from enum import Enum


class CatState(str, Enum):
    """Cat states — each maps to a GIF (or GIF pool) in ui/assets/."""
    IDLE = "idle"           # Connected, cycling random idle behaviors
    SLEEPING = "sleeping"   # Long idle — cat resting
    THINKING = "thinking"   # Processing request — cat eating
    DONE = "done"           # Task completed — cat pooping
    ERROR = "error"         # Error — cat pushing box (frustrated)
