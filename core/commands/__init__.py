"""Gateway command handlers — import all to register via @command."""

# Importing sub-modules triggers @command decorators, populating the registry.
from core.commands import utility  # noqa: F401
from core.commands import session_cmd  # noqa: F401
from core.commands import agent_cmd  # noqa: F401
from core.commands import model_cmd  # noqa: F401
from core.commands import file_cmd  # noqa: F401
from core.commands import sysauth_cmd  # noqa: F401
from core.commands import sudo_cmd  # noqa: F401
from core.commands import memory_cmd  # noqa: F401
