"""Keep real DSH QA inside its fixture home with the normal safe OS env."""
from genshin_app.agent_chat.native_runtime_environment import (
    build_native_runtime_environment,
)


def isolated_runtime_environment(profile):
    return {**build_native_runtime_environment(),
            "GENSHIN_NATIVE_DSH_PROFILE_ROOT": str(profile)}
